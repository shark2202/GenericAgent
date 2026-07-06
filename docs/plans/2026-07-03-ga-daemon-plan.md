# GA Daemon Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a golang single-binary daemon (`ga`) that wraps GenericAgent as a JSON-RPC over WebSocket gateway, with built-in GA (go:embed) and external GA (--ga-path) modes.

**Architecture:** A golang daemon manages per-session python subprocesses running `ga_shim.py` (stdio JSON-lines IPC). The daemon exposes JSON-RPC 2.0 over WebSocket. A CLI (`ga session new`, `ga session watch`, etc.) connects to the daemon as a WS client. Zero existing .py files are modified.

**Tech Stack:** golang (gorilla/websocket, cobra), Python (ga_shim.py ~80 lines using existing GenericAgent SDK), go:embed for bundling CPython runtime.

**Design Doc:** `docs/plans/2026-07-03-ga-daemon-design.md`

---

### Task 1: Go project scaffolding

**Files:**
- Create: `ga/go.mod`
- Create: `ga/main.go`
- Create: `ga/cmd/root.go`
- Create: `ga/cmd/server.go`
- Create: `ga/cmd/session.go`
- Create: `ga/cmd/llm.go`
- Create: `ga/internal/embed.go`
- Create: `ga/internal/runtime.go`
- Create: `ga/internal/session.go`
- Create: `ga/internal/session_mgr.go`
- Create: `ga/internal/jsonrpc.go`
- Create: `ga/internal/ws.go`
- Create: `ga/internal/api.schema.json`

**Step 1: Initialize go module**

Run: `go mod init ga-server`
Expected: Creates `ga/go.mod` with module `ga-server`

**Step 2: Add dependencies**

```bash
go get github.com/gorilla/websocket
go get github.com/spf13/cobra
```

Expected: `go.mod` and `go.sum` updated with dependencies.

**Step 3: Create directory structure**

```bash
mkdir -p ga/cmd ga/internal
```

**Step 4: Commit**

```bash
git add ga/go.mod ga/go.sum
git commit -m "feat(ga): scaffold go project with module and dependencies"
```

---

### Task 2: ga_shim.py — Python stdio IPC bridge

**Files:**
- Create: `ga_shim.py`

**Step 1: Write ga_shim.py**

The shim reads JSON lines from stdin, drives GenericAgent via `put_task()` + `display_queue`, and writes JSON events to stdout. It handles: prompt dispatch, cancel, ask_user INTERRUPT relay, and persistence (working.json + history.json).

```python
import os, sys, json, threading, time, uuid

GA_HOME = os.environ.get('GA_HOME', os.path.expanduser('~/.ga'))
SESSION_ID = os.environ.get('GA_SESSION_ID', '')
GA_CODE_DIR = os.environ.get('GA_CODE_DIR', os.path.join(GA_HOME, 'code'))
GA_MEMORY_DIR = os.environ.get('GA_MEMORY_DIR', os.path.join(GA_HOME, 'memory'))
GA_TEMP_DIR = os.environ.get('GA_TEMP_DIR', os.path.join(GA_HOME, 'temp'))
GA_SESSIONS_DIR = os.path.join(GA_HOME, 'sessions', SESSION_ID)

os.makedirs(GA_SESSIONS_DIR, exist_ok=True)
os.makedirs(GA_TEMP_DIR, exist_ok=True)
os.makedirs(GA_MEMORY_DIR, exist_ok=True)

sys.path.insert(0, GA_CODE_DIR)
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from agentmain import GenericAgent

def emit(evt_type, payload):
    line = json.dumps({'type': evt_type, **payload}, ensure_ascii=False)
    sys.stdout.write(line + '\n')
    sys.stdout.flush()

def save_history(history):
    path = os.path.join(GA_SESSIONS_DIR, 'history.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False)

def save_working(key_info):
    path = os.path.join(GA_SESSIONS_DIR, 'working.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'key_info': key_info}, f, ensure_ascii=False)

def save_meta(status, model=''):
    path = os.path.join(GA_SESSIONS_DIR, 'meta.json')
    meta = {
        'session_id': SESSION_ID,
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'status': status,
        'model': model,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)


agent = GenericAgent()
agent.inc_out = True
agent.verbose = False
agent.peer_hint = False

llm_no = int(os.environ.get('GA_LLM_NO', '0'))
agent.next_llm(llm_no)
model = agent.get_llm_name(model=True)
save_meta('running', model)

threading.Thread(target=agent.run, daemon=True).start()

current_msg_id = None
current_dq = None
ask_user_pending = False

def drain(dq, msg_id):
    global ask_user_pending
    last_key_info = ''
    while True:
        item = dq.get()
        if 'next' in item:
            emit('stream.chunk', {
                'session_id': SESSION_ID,
                'message_id': msg_id,
                'delta': item['next'],
                'turn': item.get('turn', 0),
            })
        if 'done' in item:
            done_data = item['done']
            if isinstance(done_data, str) and 'INTERRUPT' in done_data:
                try:
                    parsed = json.loads(done_data)
                    if parsed.get('status') == 'INTERRUPT':
                        data = parsed.get('data', {})
                        emit('ask_user.request', {
                            'session_id': SESSION_ID,
                            'message_id': msg_id,
                            'question': data.get('question', ''),
                            'candidates': data.get('candidates', []),
                        })
                        ask_user_pending = True
                        return
                except (json.JSONDecodeError, AttributeError):
                    pass
            emit('stream.done', {
                'session_id': SESSION_ID,
                'message_id': msg_id,
                'full': done_data if isinstance(done_data, str) else json.dumps(done_data, ensure_ascii=False),
                'turn': item.get('turn', 0),
            })
            save_history(agent.llmclient.backend.history)
            if agent.handler and 'key_info' in agent.handler.working:
                save_working(agent.handler.working.get('key_info', ''))
            return

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except json.JSONDecodeError:
        continue

    msg_id = req.get('id', str(uuid.uuid4())[:8])

    if req.get('type') == 'prompt':
        ask_user_pending = False
        current_msg_id = msg_id
        dq = agent.put_task(req['content'], source='user', images=req.get('images'))
        drain(dq, msg_id)

    elif req.get('type') == 'cancel':
        agent.abort()

    elif req.get('type') == 'llm_switch':
        agent.next_llm(int(req.get('llm_no', 0)))
        emit('session.event', {
            'session_id': SESSION_ID,
            'type': 'llm_changed',
            'detail': agent.get_llm_name(model=True),
        })

    elif req.get('type') == 'shutdown':
        agent.abort()
        save_meta('closed', model)
        break
```

**Step 2: Verify ga_shim.py imports work**

```bash
python -c "import ga_shim"
```

Note: This will fail at import time because GenericAgent triggers mykey loading, but the import structure should be valid Python. Focus on syntax correctness.

**Step 3: Commit**

```bash
git add ga_shim.py
git commit -m "feat(ga): add ga_shim.py stdio IPC bridge for golang daemon"
```

---

### Task 3: Go embed declarations and runtime extraction

**Files:**
- Modify: `ga/internal/embed.go`
- Modify: `ga/internal/runtime.go`

**Step 1: Write embed.go**

```go
package internal

import "embed"

//go:embed all:../../dist/python-bundle/win-x64/python
var pythonRuntimeWin embed.FS

//go:embed ../../ga_shim.py
var gaShim []byte

//go:embed api.schema.json
var apiSchema []byte
```

Note: The embed paths are relative to `ga/internal/`. For the initial build, we only embed the current platform. The CI matrix handles multi-platform. The `api.schema.json` is in the same `internal/` directory.

**Step 2: Write runtime.go**

```go
package internal

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
)

func pythonBin() string {
	if runtime.GOOS == "windows" {
		return "python.exe"
	}
	return "bin/python3"
}

func ExtractRuntime(gaHome string) (string, error) {
	runtimeDir := filepath.Join(gaHome, "runtime", runtime.GOOS+"-"+runtime.GOARCH)
	marker := filepath.Join(runtimeDir, "python", ".extracted")

	if _, err := os.Stat(marker); err == nil {
		bin := filepath.Join(runtimeDir, "python", pythonBin())
		return bin, nil
	}

	if err := os.RemoveAll(runtimeDir); err != nil {
		return "", fmt.Errorf("clean runtime dir: %w", err)
	}
	if err := os.MkdirAll(runtimeDir, 0755); err != nil {
		return "", fmt.Errorf("create runtime dir: %w", err)
	}

	// Extract embedded python runtime
	err := extractFS(pythonRuntimeWin, "dist/python-bundle/win-x64/python", runtimeDir+"/python")
	if err != nil {
		return "", fmt.Errorf("extract python runtime: %w", err)
	}

	// Write marker
	os.WriteFile(marker, []byte("1"), 0644)

	bin := filepath.Join(runtimeDir, "python", pythonBin())
	if err := os.Chmod(bin, 0755); err != nil {
		return "", fmt.Errorf("chmod python: %w", err)
	}
	return bin, nil
}

func extractFS(fs embed.FS, prefix, dest string) error {
	// walk embedded FS and copy files to disk
	// ... implementation
}
```

**Step 3: Commit**

```bash
git add ga/internal/embed.go ga/internal/runtime.go
git commit -m "feat(ga): add go:embed declarations and runtime extraction"
```

---

### Task 4: Session management

**Files:**
- Modify: `ga/internal/session.go`
- Modify: `ga/internal/session_mgr.go`

**Step 1: Write session.go**

```go
package internal

import (
	"bufio"
	"encoding/json"
	"os/exec"
	"sync"
	"time"
)

type Session struct {
	ID        string
	Status    string   // "running", "idle", "closed", "crashed"
	Model     string
	CreatedAt time.Time

	cmd    *exec.Cmd
	stdin  io.WriteCloser
	stdout *bufio.Scanner
	mu     sync.Mutex
	msgSeq int64

	// Callbacks
	OnEvent func(sessionID string, evt json.RawMessage)
}

func (s *Session) Send(req json.RawMessage) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	_, err := s.stdin.Write(append(req, '\n'))
	return err
}

func (s *Session) NextMsgID() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.msgSeq++
	return fmt.Sprintf("%s-%d", s.ID[:8], s.msgSeq)
}

func (s *Session) Kill() {
	if s.cmd != nil && s.cmd.Process != nil {
		s.cmd.Process.Signal(os.Interrupt)
		time.Sleep(3 * time.Second)
		s.cmd.Process.Kill()
	}
}
```

**Step 2: Write session_mgr.go**

```go
package internal

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"sync"
	"time"
)

type SessionMgr struct {
	sessions sync.Map // sessionID -> *Session
	gaHome   string
	pythonBin string
	gaCodeDir string
	gaMemoryDir string
	gaTempDir string
}

func NewSessionMgr(gaHome string) *SessionMgr {
	return &SessionMgr{
		gaHome: gaHome,
	}
}

func (m *SessionMgr) Create(gaPath string, llmNo int, resumeFrom string) (*Session, error) {
	sessionID := generateSessionID()
	sessionDir := filepath.Join(m.gaHome, "sessions", sessionID)
	os.MkdirAll(sessionDir, 0755)

	// Determine python binary and GA code dir
	var pythonBin, gaCodeDir, gaMemoryDir string
	if gaPath != "" {
		pythonBin = findPythonInGA(gaPath)
		gaCodeDir = gaPath
		gaMemoryDir = filepath.Join(gaPath, "memory")
	} else {
		bin, err := ExtractRuntime(m.gaHome)
		if err != nil {
			return nil, err
		}
		pythonBin = bin
		gaCodeDir = m.gaHome + "/code"
		gaMemoryDir = m.gaHome + "/memory"
	}
	gaTempDir := filepath.Join(m.gaHome, "temp")

	cmd := exec.Command(pythonBin, gaCodeDir+"/ga_shim.py")
	cmd.Env = append(os.Environ(),
		"GA_HOME="+m.gaHome,
		"GA_SESSION_ID="+sessionID,
		"GA_CODE_DIR="+gaCodeDir,
		"GA_MEMORY_DIR="+gaMemoryDir,
		"GA_TEMP_DIR="+gaTempDir,
		"GA_LLM_NO="+fmt.Sprintf("%d", llmNo),
	)

	stdin, _ := cmd.StdinPipe()
	stdout, _ := cmd.StdoutPipe()

	cmd.Start()

	s := &Session{
		ID:        sessionID,
		Status:    "running",
		CreatedAt: time.Now(),
		cmd:       cmd,
		stdin:     stdin,
		stdout:    bufio.NewScanner(stdout),
	}
	m.sessions.Store(sessionID, s)

	// Start stdout reader goroutine
	go func() {
		for s.stdout.Scan() {
			line := s.stdout.Text()
			if s.OnEvent != nil {
				s.OnEvent(sessionID, json.RawMessage(line))
			}
		}
		cmd.Wait()
		s.Status = "closed"
		// write meta.json status=closed
	}()

	// Write meta.json
	writeMeta(sessionDir, sessionID, "running", "")

	return s, nil
}

func (m *SessionMgr) Get(id string) (*Session, bool) {
	v, ok := m.sessions.Load(id)
	if !ok {
		return nil, false
	}
	return v.(*Session), true
}

func (m *SessionMgr) List() []*Session {
	var result []*Session
	m.sessions.Range(func(k, v interface{}) bool {
		result = append(result, v.(*Session))
		return true
	})
	return result
}

func (m *SessionMgr) Close(id string) error {
	s, ok := m.Get(id)
	if !ok {
		return fmt.Errorf("session not found: %s", id)
	}
	s.Send(json.RawMessage(`{"type":"shutdown"}`))
	s.Kill()
	m.sessions.Delete(id)
	return nil
}
```

**Step 3: Commit**

```bash
git add ga/internal/session.go ga/internal/session_mgr.go
git commit -m "feat(ga): implement session management and per-session python subprocess"
```

---

### Task 5: JSON-RPC 2.0 codec and API schema

**Files:**
- Modify: `ga/internal/jsonrpc.go`
- Modify: `ga/internal/api.schema.json`

**Step 1: Write api.schema.json**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "GA Daemon API",
  "version": "0.1.0",
  "methods": {
    "session.create": {
      "params": {
        "type": "object",
        "properties": {
          "resume_from": {"type": "string"},
          "llm_no": {"type": "integer", "default": 0},
          "ga_path": {"type": "string"}
        }
      },
      "result": {
        "type": "object",
        "properties": {
          "session_id": {"type": "string"}
        },
        "required": ["session_id"]
      }
    },
    "session.close": {
      "params": {
        "type": "object",
        "properties": {
          "session_id": {"type": "string"}
        },
        "required": ["session_id"]
      },
      "result": {
        "type": "object",
        "properties": {
          "ok": {"type": "boolean"}
        }
      }
    },
    "session.list": {
      "params": {"type": "object"},
      "result": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "session_id": {"type": "string"},
            "status": {"type": "string"},
            "model": {"type": "string"},
            "created_at": {"type": "string"}
          }
        }
      }
    },
    "message.send": {
      "params": {
        "type": "object",
        "properties": {
          "session_id": {"type": "string"},
          "content": {"type": "string"},
          "images": {
            "type": "array",
            "items": {"type": "string"}
          }
        },
        "required": ["session_id", "content"]
      },
      "result": {
        "type": "object",
        "properties": {
          "message_id": {"type": "string"}
        }
      }
    },
    "stream.cancel": {
      "params": {
        "type": "object",
        "properties": {
          "session_id": {"type": "string"}
        },
        "required": ["session_id"]
      },
      "result": {
        "type": "object",
        "properties": {
          "ok": {"type": "boolean"}
        }
      }
    },
    "llm.list": {
      "params": {"type": "object"},
      "result": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "no": {"type": "integer"},
            "name": {"type": "string"},
            "active": {"type": "boolean"}
          }
        }
      }
    },
    "llm.switch": {
      "params": {
        "type": "object",
        "properties": {
          "session_id": {"type": "string"},
          "llm_no": {"type": "integer"}
        },
        "required": ["session_id", "llm_no"]
      },
      "result": {
        "type": "object",
        "properties": {
          "name": {"type": "string"}
        }
      }
    }
  },
  "notifications": {
    "stream.chunk": {
      "params": {
        "type": "object",
        "properties": {
          "session_id": {"type": "string"},
          "message_id": {"type": "string"},
          "delta": {"type": "string"},
          "turn": {"type": "integer"}
        }
      }
    },
    "stream.done": {
      "params": {
        "type": "object",
        "properties": {
          "session_id": {"type": "string"},
          "message_id": {"type": "string"},
          "full": {"type": "string"},
          "turn": {"type": "integer"}
        }
      }
    },
    "ask_user.request": {
      "params": {
        "type": "object",
        "properties": {
          "session_id": {"type": "string"},
          "message_id": {"type": "string"},
          "question": {"type": "string"},
          "candidates": {
            "type": "array",
            "items": {"type": "string"}
          }
        }
      }
    },
    "session.event": {
      "params": {
        "type": "object",
        "properties": {
          "session_id": {"type": "string"},
          "type": {"type": "string"},
          "detail": {"type": "string"}
        }
      }
    }
  }
}
```

**Step 2: Write jsonrpc.go**

```go
package internal

import (
	"encoding/json"
	"fmt"
)

type RPCRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      interface{}     `json:"id"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
}

type RPCResponse struct {
	JSONRPC string      `json:"jsonrpc"`
	ID      interface{} `json:"id"`
	Result  interface{} `json:"result,omitempty"`
	Error   *RPCError   `json:"error,omitempty"`
}

type RPCNotification struct {
	JSONRPC string          `json:"jsonrpc"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
}

type RPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

func ParseRequest(data []byte) (*RPCRequest, error) {
	var req RPCRequest
	if err := json.Unmarshal(data, &req); err != nil {
		return nil, err
	}
	if req.JSONRPC != "2.0" {
		return nil, fmt.Errorf("invalid jsonrpc version")
	}
	return &req, nil
}

func NewResponse(id interface{}, result interface{}) *RPCResponse {
	return &RPCResponse{
		JSONRPC: "2.0",
		ID:      id,
		Result:  result,
	}
}

func NewErrorResponse(id interface{}, code int, msg string) *RPCResponse {
	return &RPCResponse{
		JSONRPC: "2.0",
		ID:      id,
		Error:   &RPCError{Code: code, Message: msg},
	}
}

func NewNotification(method string, params interface{}) (*RPCNotification, error) {
	p, err := json.Marshal(params)
	if err != nil {
		return nil, err
	}
	return &RPCNotification{
		JSONRPC: "2.0",
		Method:  method,
		Params:  p,
	}, nil
}
```

**Step 3: Commit**

```bash
git add ga/internal/jsonrpc.go ga/internal/api.schema.json
git commit -m "feat(ga): add JSON-RPC 2.0 codec and API schema"
```

---

### Task 6: WebSocket handler

**Files:**
- Modify: `ga/internal/ws.go`

**Step 1: Write ws.go**

```go
package internal

import (
	"encoding/json"
	"log"
	"net/http"

	"github.com/gorilla/websocket"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

type WSServer struct {
	mgr *SessionMgr
}

func NewWSServer(mgr *SessionMgr) *WSServer {
	return &WSServer{mgr: mgr}
}

func (s *WSServer) HandleWS(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("ws upgrade error: %v", err)
		return
	}
	defer conn.Close()

	for {
		_, msg, err := conn.ReadMessage()
		if err != nil {
			break
		}

		req, err := ParseRequest(msg)
		if err != nil {
			resp := NewErrorResponse(nil, -32700, "parse error")
			writeJSON(conn, resp)
			continue
		}

		// Dispatch by method
		switch req.Method {
		case "session.create":
			s.handleSessionCreate(conn, req)
		case "session.close":
			s.handleSessionClose(conn, req)
		case "session.list":
			s.handleSessionList(conn, req)
		case "message.send":
			s.handleMessageSend(conn, req)
		case "stream.cancel":
			s.handleStreamCancel(conn, req)
		case "llm.list":
			s.handleLLMList(conn, req)
		case "llm.switch":
			s.handleLLMSwitch(conn, req)
		default:
			resp := NewErrorResponse(req.ID, -32601, "method not found: "+req.Method)
			writeJSON(conn, resp)
		}
	}
}

func (s *WSServer) handleSessionCreate(conn *websocket.Conn, req *RPCRequest) {
	var params struct {
		ResumeFrom string `json:"resume_from"`
		LLMNo      int    `json:"llm_no"`
		GAPath     string `json:"ga_path"`
	}
	json.Unmarshal(req.Params, &params)

	session, err := s.mgr.Create(params.GAPath, params.LLMNo, params.ResumeFrom)
	if err != nil {
		writeJSON(conn, NewErrorResponse(req.ID, -32000, err.Error()))
		return
	}

	// Route shim events back to this WS connection
	session.OnEvent = func(sid string, evt json.RawMessage) {
		conn.WriteMessage(websocket.TextMessage, evt)
	}

	writeJSON(conn, NewResponse(req.ID, map[string]string{"session_id": session.ID}))
}

func (s *WSServer) handleSessionClose(conn *websocket.Conn, req *RPCRequest) {
	var params struct {
		SessionID string `json:"session_id"`
	}
	json.Unmarshal(req.Params, &params)
	err := s.mgr.Close(params.SessionID)
	if err != nil {
		writeJSON(conn, NewErrorResponse(req.ID, -32000, err.Error()))
		return
	}
	writeJSON(conn, NewResponse(req.ID, map[string]bool{"ok": true}))
}

func (s *WSServer) handleSessionList(conn *websocket.Conn, req *RPCRequest) {
	sessions := s.mgr.List()
	var result []map[string]interface{}
	for _, s := range sessions {
		result = append(result, map[string]interface{}{
			"session_id": s.ID,
			"status":     s.Status,
			"model":      s.Model,
			"created_at": s.CreatedAt.Format(time.RFC3339),
		})
	}
	writeJSON(conn, NewResponse(req.ID, result))
}

func (s *WSServer) handleMessageSend(conn *websocket.Conn, req *RPCRequest) {
	var params struct {
		SessionID string   `json:"session_id"`
		Content   string   `json:"content"`
		Images    []string `json:"images"`
	}
	json.Unmarshal(req.Params, &params)

	session, ok := s.mgr.Get(params.SessionID)
	if !ok {
		writeJSON(conn, NewErrorResponse(req.ID, -32000, "session not found"))
		return
	}

	msgID := session.NextMsgID()
	msg := map[string]interface{}{
		"type":    "prompt",
		"id":      msgID,
		"content": params.Content,
	}
	if len(params.Images) > 0 {
		msg["images"] = params.Images
	}
	raw, _ := json.Marshal(msg)
	session.Send(raw)

	writeJSON(conn, NewResponse(req.ID, map[string]string{"message_id": msgID}))
}

func (s *WSServer) handleStreamCancel(conn *websocket.Conn, req *RPCRequest) {
	var params struct {
		SessionID string `json:"session_id"`
	}
	json.Unmarshal(req.Params, &params)

	session, ok := s.mgr.Get(params.SessionID)
	if !ok {
		writeJSON(conn, NewErrorResponse(req.ID, -32000, "session not found"))
		return
	}
	session.Send(json.RawMessage(`{"type":"cancel"}`))
	writeJSON(conn, NewResponse(req.ID, map[string]bool{"ok": true}))
}

func (s *WSServer) handleLLMList(conn *websocket.Conn, req *RPCRequest) {
	// LLM list is not available from daemon without a session
	// Return empty for now; client can query via session
	writeJSON(conn, NewResponse(req.ID, []interface{}{}))
}

func (s *WSServer) handleLLMSwitch(conn *websocket.Conn, req *RPCRequest) {
	var params struct {
		SessionID string `json:"session_id"`
		LLMNo     int    `json:"llm_no"`
	}
	json.Unmarshal(req.Params, &params)

	session, ok := s.mgr.Get(params.SessionID)
	if !ok {
		writeJSON(conn, NewErrorResponse(req.ID, -32000, "session not found"))
		return
	}
	msg, _ := json.Marshal(map[string]interface{}{
		"type":   "llm_switch",
		"llm_no": params.LLMNo,
	})
	session.Send(msg)
	writeJSON(conn, NewResponse(req.ID, map[string]string{"name": "switched"}))
}

func writeJSON(conn *websocket.Conn, v interface{}) {
	conn.WriteJSON(v)
}
```

**Step 2: Commit**

```bash
git add ga/internal/ws.go
git commit -m "feat(ga): implement WebSocket handler with JSON-RPC method dispatch"
```

---

### Task 7: CLI commands (cobra)

**Files:**
- Modify: `ga/main.go`
- Modify: `ga/cmd/root.go`
- Modify: `ga/cmd/server.go`
- Modify: `ga/cmd/session.go`
- Modify: `ga/cmd/llm.go`

**Step 1: Write main.go**

```go
package main

import "ga-server/cmd"

func main() {
	cmd.Execute()
}
```

**Step 2: Write root.go**

```go
package cmd

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"
)

var Version = "0.1.0"

var rootCmd = &cobra.Command{
	Use:   "ga",
	Short: "GenericAgent daemon — single-binary AI agent gateway",
}

func Execute() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
```

**Step 3: Write server.go**

```go
package cmd

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"

	"ga-server/internal"

	"github.com/spf13/cobra"
)

var (
	gaPath  string
	bind    string
)

var serverCmd = &cobra.Command{
	Use:   "server start",
	Short: "Start the GA daemon",
	RunE: func(cmd *cobra.Command, args []string) error {
		gaHome := os.Getenv("GA_HOME")
		if gaHome == "" {
			home, _ := os.UserHomeDir()
			gaHome = home + "/.ga"
		}

		mgr := internal.NewSessionMgr(gaHome)
		ws := internal.NewWSServer(mgr)

		http.HandleFunc("/ws", ws.HandleWS)

		// Graceful shutdown
		go func() {
			sigCh := make(chan os.Signal, 1)
			signal.Notify(sigCh, os.Interrupt)
			<-sigCh
			log.Println("shutting down...")
			os.Exit(0)
		}()

		fmt.Printf("GA daemon listening on %s\n", bind)
		return http.ListenAndServe(bind, nil)
	},
}

func init() {
	serverCmd.Flags().StringVar(&gaPath, "ga-path", "", "Path to external GenericAgent installation")
	serverCmd.Flags().StringVar(&bind, "bind", "127.0.0.1:7331", "Bind address")
	rootCmd.AddCommand(serverCmd)
}
```

**Step 4: Write session.go**

```go
package cmd

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"

	"github.com/gorilla/websocket"
	"github.com/spf13/cobra"
)

var sessionCmd = &cobra.Command{
	Use:   "session",
	Short: "Manage GA sessions",
}

var sessionNewCmd = &cobra.Command{
	Use:   "new [prompt]",
	Short: "Create a new session and send a prompt",
	Args:  cobra.MinimumNArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		conn, err := connectDaemon()
		if err != nil {
			return err
		}
		defer conn.Close()

		// 1. Create session
		createResp, err := rpcCall(conn, "session.create", map[string]interface{}{})
		if err != nil {
			return err
		}
		var createResult struct {
			SessionID string `json:"session_id"`
		}
		json.Unmarshal(createResp.Result, &createResult)

		// 2. Send message
		sendResp, err := rpcCall(conn, "message.send", map[string]interface{}{
			"session_id": createResult.SessionID,
			"content":    args[0],
		})
		if err != nil {
			return err
		}
		var sendResult struct {
			MessageID string `json:"message_id"`
		}
		json.Unmarshal(sendResp.Result, &sendResult)

		// 3. Stream output
		for {
			_, msg, err := conn.ReadMessage()
			if err != nil {
				break
			}
			var evt struct {
				Type string `json:"type"`
			}
			json.Unmarshal(msg, &evt)
			switch evt.Type {
			case "stream.chunk":
				var chunk struct {
					Delta string `json:"delta"`
				}
				json.Unmarshal(msg, &chunk)
				fmt.Print(chunk.Delta)
			case "stream.done":
				fmt.Println()
				return nil
			case "ask_user.request":
				var ask struct {
					Question   string   `json:"question"`
					Candidates []string `json:"candidates"`
				}
				json.Unmarshal(msg, &ask)
				fmt.Printf("\n[ASK] %s\n", ask.Question)
				if len(ask.Candidates) > 0 {
					for i, c := range ask.Candidates {
						fmt.Printf("  %d. %s\n", i+1, c)
					}
				}
				fmt.Print("> ")
				// Read reply from stdin and send back
				// ...
			}
		}
		return nil
	},
}

func connectDaemon() (*websocket.Conn, error) {
	u := url.URL{Scheme: "ws", Host: "127.0.0.1:7331", Path: "/ws"}
	conn, _, err := websocket.DefaultDialer.Dial(u.String(), nil)
	return conn, err
}

func rpcCall(conn *websocket.Conn, method string, params interface{}) (*internal.RPCResponse, error) {
	req := internal.RPCRequest{
		JSONRPC: "2.0",
		ID:      1,
		Method:  method,
	}
	p, _ := json.Marshal(params)
	req.Params = p

	conn.WriteJSON(req)
	_, msg, err := conn.ReadMessage()
	if err != nil {
		return nil, err
	}
	var resp internal.RPCResponse
	json.Unmarshal(msg, &resp)
	return &resp, nil
}
```

**Step 5: Commit**

```bash
git add ga/main.go ga/cmd/root.go ga/cmd/server.go ga/cmd/session.go ga/cmd/llm.go
git commit -m "feat(ga): implement CLI commands with cobra (server, session, llm)"
```

---

### Task 8: Build integration

**Files:**
- Create: `scripts/build-ga.sh`
- Modify: `ga/internal/runtime.go` (fix embed paths, complete extractFS)

**Step 1: Write build script**

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="${1:-$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)}"

# Normalize arch
case "$ARCH" in
  darwin-arm64|macos-arm64) ARCH="mac-arm64" ;;
  darwin-x64|macos-x64)     ARCH="mac-x64" ;;
  linux-x64|linux-amd64)    ARCH="linux-x64" ;;
  windows-x64|win-x64)      ARCH="win-x64" ;;
esac

echo "Building GA daemon for $ARCH"

# Step 1: Build python bundle
bash "$REPO_ROOT/scripts/bundle-python.sh" "$ARCH"

# Step 2: Copy GA code to dist for embedding
rm -rf "$REPO_ROOT/dist/ga-code"
mkdir -p "$REPO_ROOT/dist/ga-code"
cp "$REPO_ROOT/ga_shim.py" "$REPO_ROOT/dist/ga-code/"
cp "$REPO_ROOT/agentmain.py" "$REPO_ROOT/dist/ga-code/"
cp "$REPO_ROOT/ga.py" "$REPO_ROOT/dist/ga-code/"
cp "$REPO_ROOT/agent_loop.py" "$REPO_ROOT/dist/ga-code/"
cp "$REPO_ROOT/llmcore.py" "$REPO_ROOT/dist/ga-code/"
cp "$REPO_ROOT/simphtml.py" "$REPO_ROOT/dist/ga-code/"
cp "$REPO_ROOT/TMWebDriver.py" "$REPO_ROOT/dist/ga-code/"
cp -r "$REPO_ROOT/memory" "$REPO_ROOT/dist/ga-code/"
cp -r "$REPO_ROOT/assets" "$REPO_ROOT/dist/ga-code/"

# Step 3: Build go binary
cd "$REPO_ROOT/ga"
go build -ldflags "-X ga-server/cmd.Version=0.1.0" -o "$REPO_ROOT/dist/ga" .
echo "Done: dist/ga"
```

**Step 2: Set up embed.go to use dist/ga-code and dist/python-bundle**

Adjust embed paths to point to `dist/ga-code/` and `dist/python-bundle/`. The `dist/` directory is populated by the build script before `go build`.

**Step 3: Commit**

```bash
git add scripts/build-ga.sh
git commit -m "feat(ga): add build script wiring python bundle + go build"
```

---

### Task 9: End-to-end integration test

**Step 1: Build the binary**

```bash
bash scripts/build-ga.sh win-x64
```

Expected: `dist/ga` binary produced.

**Step 2: Start daemon**

```bash
./dist/ga server start --bind 127.0.0.1:7331
```

Expected: "GA daemon listening on 127.0.0.1:7331"

**Step 3: Create session and send prompt**

In another terminal:
```bash
./dist/ga session new "echo hello"
```

Expected: Streaming output, then session completes.

**Step 4: Verify session persistence**

Check `$GA_HOME/sessions/` for `history.json`, `working.json`, `meta.json`.

**Step 5: Commit**

```bash
git commit --allow-empty -m "feat(ga): verify end-to-end daemon integration"
```

---

### Task 10: CI matrix build (GitHub Actions)

**Files:**
- Create: `.github/workflows/build-ga.yml`

**Step 1: Write GitHub Actions workflow**

```yaml
name: Build GA Daemon

on:
  push:
    tags: ['v*']

jobs:
  build:
    strategy:
      matrix:
        include:
          - arch: win-x64
            os: windows-latest
            ext: .exe
          - arch: mac-arm64
            os: macos-latest
            ext: ''
          - arch: linux-x64
            os: ubuntu-latest
            ext: ''
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: '1.22'
      - name: Build python bundle
        run: bash scripts/bundle-python.sh ${{ matrix.arch }}
      - name: Build ga binary
        run: bash scripts/build-ga.sh ${{ matrix.arch }}
      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: ga-${{ matrix.arch }}
          path: dist/ga${{ matrix.ext }}
```

**Step 2: Commit**

```bash
git add .github/workflows/build-ga.yml
git commit -m "ci(ga): add GitHub Actions matrix build for win/mac/linux"
```

---

## Summary

| Task | Component | New Files | Modified Files |
|------|-----------|-----------|----------------|
| 1 | Go scaffolding | `ga/go.mod`, `ga/main.go`, `ga/cmd/*.go`, `ga/internal/*.go` | — |
| 2 | ga_shim.py | `ga_shim.py` | — |
| 3 | Embed + runtime | — | `ga/internal/embed.go`, `ga/internal/runtime.go` |
| 4 | Session mgmt | — | `ga/internal/session.go`, `ga/internal/session_mgr.go` |
| 5 | JSON-RPC | — | `ga/internal/jsonrpc.go`, `ga/internal/api.schema.json` |
| 6 | WebSocket | — | `ga/internal/ws.go` |
| 7 | CLI commands | — | `ga/main.go`, `ga/cmd/*.go` |
| 8 | Build script | `scripts/build-ga.sh` | `ga/internal/runtime.go`, `ga/internal/embed.go` |
| 9 | E2E test | — | — |
| 10 | CI | `.github/workflows/build-ga.yml` | — |

**Zero existing .py files modified.** Only `ga_shim.py` is new.