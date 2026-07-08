package llm

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// LoadMykeys 读取 mykey.jsonc（或 mykey.json），解析为 cfg_name → cfg map。
//
// 替代 Python llmcore._load_mykeys 的 `import mykey` 子进程方案：mykey 实际是纯静态
// dict 赋值（无动态 Python 逻辑），JSONC 无损等价，且让 LLM 路径（mykey + OAI Session）
// 完全纯 Go，不依赖 python3/bundle。
//
// ponytail: 仅本地 JSONC/JSON；remote_url 远端 key 分发未实现，add when 需要中央分发。
func LoadMykeys(root string) (map[string]map[string]any, error) {
	path := filepath.Join(root, "mykey.jsonc")
	if _, err := os.Stat(path); err != nil {
		path = filepath.Join(root, "mykey.json")
	}
	src, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("mykey load failed: %w (需 mykey.jsonc，参考 mykey_template.jsonc)", err)
	}
	cfgs := map[string]map[string]any{}
	if err := json.Unmarshal(stripJSONC(src), &cfgs); err != nil {
		return nil, fmt.Errorf("mykey parse %s: %w", path, err)
	}
	return cfgs, nil
}

// ResolveCfg 取指定 cfg_name 的配置 map。对应 Python resolve_session(cfg_name) 的 cfg 查找部分。
func ResolveCfg(root, cfgName string) (map[string]any, error) {
	cfgs, err := LoadMykeys(root)
	if err != nil {
		return nil, err
	}
	cfg, ok := cfgs[cfgName]
	if !ok {
		avail := make([]string, 0, len(cfgs))
		for k := range cfgs {
			avail = append(avail, k)
		}
		return nil, fmt.Errorf("cfg %q not in mykey (available: %v)", cfgName, avail)
	}
	return cfg, nil
}

// stripJSONC 去除 // 行注释与 /* */ 块注释。字符串内不处理——apibase 里的 https:// 安全。
// ponytail: 不支持尾逗号（要求合法 JSON + 注释）；模板即此格式，避免字符串内 ",}" 误删。
func stripJSONC(src []byte) []byte {
	var out bytes.Buffer
	out.Grow(len(src))
	inStr := false
	for i := 0; i < len(src); {
		c := src[i]
		if inStr {
			out.WriteByte(c)
			if c == '\\' && i+1 < len(src) {
				out.WriteByte(src[i+1])
				i += 2
				continue
			}
			if c == '"' {
				inStr = false
			}
			i++
			continue
		}
		switch {
		case c == '"':
			inStr = true
			out.WriteByte(c)
			i++
		case c == '/' && i+1 < len(src) && src[i+1] == '/':
			for i < len(src) && src[i] != '\n' {
				i++
			}
		case c == '/' && i+1 < len(src) && src[i+1] == '*':
			i += 2
			for i+1 < len(src) && !(src[i] == '*' && src[i+1] == '/') {
				i++
			}
			i += 2
			if i > len(src) {
				i = len(src)
			}
		default:
			out.WriteByte(c)
			i++
		}
	}
	return out.Bytes()
}
