import sys, os, re, json, threading
if sys.stdout is None: sys.stdout = open(os.devnull, "w")
if sys.stderr is None: sys.stderr = open(os.devnull, "w")
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent_loop import BaseHandler, StepOutcome, json_default
from skill_loader import sync_skills_to_l1, get_skill_detail
from ga_utils import (
    safe_print, code_run, ask_user, first_init_driver, web_scan, web_execute_js,
    format_error, log_memory_access, expand_file_refs, file_patch, _scan_files,
    file_read, smart_format, consume_file, script_dir, driver, _read_dirs,
)
# Trigger self-registration of migrated tools (e.g. skill_manage) into the
# agent_loop registry. `import *` is intentional: it both loads the module
# (running @register_tool at module scope) and re-exports public helpers.
from tools.skill_manage import *  # noqa: F401,F403
# Re-export moved utils so external `from ga import smart_format, ...` (agentmain.py:14)
# keeps working after the extraction. `__all__` also silences F401 on re-exports.
__all__ = [
    "GenericAgentHandler", "get_global_memory",
    "safe_print", "code_run", "ask_user", "first_init_driver", "web_scan",
    "web_execute_js", "format_error", "log_memory_access", "expand_file_refs",
    "file_patch", "_scan_files", "file_read", "smart_format", "consume_file",
    "script_dir", "driver", "_read_dirs",
]

class GenericAgentHandler(BaseHandler):
    '''Generic Agent 工具库，包含多种工具的实现。工具函数自动加上了 do_ 前缀。实际工具名没有前缀。'''
    def __init__(self, parent, last_history=None, cwd='./temp'):
        self.parent = parent
        self.working = {}
        self.cwd = cwd;  self.current_turn = 0
        self.history_info = last_history if last_history else []
        self.code_stop_signal = []
        self._done_hooks = []
        self._pending_briefs = []        # self-evolution: queued skill-distillation briefs (flushed at turn%10 / task end)
        self._evolution_signal = None    # self-evolution: accumulated negative signal (errors/retries/corrections) for v1.5 trigger
        # subagent manager (first-class subagents, add-first-class-subagents):
        # try/except 防御 —— subagents 未合并到 dev 时 ImportError, _subagent_mgr=None,
        # skill-scoring gate 缺省走 InProcessScorer(本 change 可独立落地)。
        # subagents 合并后无需改本行即启用 SubagentScorer 真隔离。
        try:
            from subagent_manager import SubagentManager
            self._subagent_mgr = SubagentManager(root=self.cwd)
        except ImportError:
            self._subagent_mgr = None
        self.print = safe_print

    def _get_abs_path(self, path):
        if not path: return ""
        return os.path.abspath(os.path.join(self.cwd, path))   

    def _extract_code_block(self, response, code_type):
        code_type = {'python':'python|py', 'powershell':'powershell|ps1|pwsh', 'bash':'bash|sh|shell'}.get(code_type, re.escape(code_type))
        matches = re.findall(rf"```(?:{code_type})\n(.*?)\n```", response.content, re.DOTALL)
        return matches[-1].strip() if matches else None

    def do_code_run(self, args, response):
        '''执行代码片段，有长度限制，不允许代码中放大量数据，如有需要应当通过文件读取进行。'''
        code_type = args.get("type", "python")
        code = args.get("code") or args.get("script")
        if not code:
            code = self._extract_code_block(response, code_type)
            if not code: return StepOutcome("[Error] Code missing. Must use reply code block or 'script' arg.", next_prompt="\n")
        try: timeout = int(args.get("timeout", 60))
        except: timeout = 60
        raw_path = os.path.join(self.cwd, args.get("cwd", './'))
        cwd = os.path.normpath(os.path.abspath(raw_path))
        code_cwd = os.path.normpath(self.cwd)
        maxlen = 10000 // args.get('_tool_num', 1)
        if code_type == 'python' and args.get("inline_eval"):
            ns = {'handler':self, 'parent':self.parent, 'history':json.dumps(self.parent.llmclient.backend.history)}
            old_cwd = os.getcwd()
            try:
                os.chdir(cwd)
                try:
                    try: result = repr(eval(code, ns))
                    except SyntaxError: exec(code, ns); result = ns.get('_r', 'OK')
                except Exception as e: result = f'Error: {e}'
            finally: os.chdir(old_cwd)
        else: result = yield from code_run(code, code_type, timeout, cwd, code_cwd=code_cwd, stop_signal=self.code_stop_signal, maxlen=maxlen, myprint=self.print)
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome(result, next_prompt=next_prompt)
    
    def do_ask_user(self, args, response):
        question = args.get("question", "请提供输入：")
        candidates = args.get("candidates", [])
        result = ask_user(question, candidates)
        yield f"Waiting for your answer ...\n"
        return StepOutcome(result, next_prompt="", should_exit=True)
    
    def do_web_scan(self, args, response):
        '''获取当前页面内容和标签页列表。也可用于切换标签页。
        注意：HTML经过简化，边栏/浮动元素等可能被过滤。如需查看被过滤的内容请用execute_js。
        tabs_only=true时仅返回标签页列表，不获取HTML（省token）'''
        tabs_only = args.get("tabs_only", False)
        switch_tab_id = args.get("switch_tab_id", None)
        text_only = args.get("text_only", False)
        maxlen = 35000 // args.get('_tool_num', 1)
        result = web_scan(tabs_only=tabs_only, switch_tab_id=switch_tab_id, text_only=text_only, maxlen=maxlen)
        content = result.pop("content", None)
        yield f'[Info] {str(result)}\n'
        if content: result = json.dumps(result, ensure_ascii=False, default=json_default) + f"\n```html\n{content}\n```"
        next_prompt = "\n"
        return StepOutcome(result, next_prompt=next_prompt)
    
    def do_web_execute_js(self, args, response):
        '''web情况下的优先使用工具，执行任何js达成对浏览器的*完全*控制。支持将结果保存到文件供后续读取分析。'''
        script = args.get("script", "") or self._extract_code_block(response, "javascript")
        if not script: return StepOutcome("[Error] Script missing. Use ```javascript block or 'script' arg.", next_prompt="\n")
        abs_path = self._get_abs_path(script.strip())
        if os.path.isfile(abs_path):
            with open(abs_path, 'r', encoding='utf-8') as f: script = f.read()
        save_to_file = args.get("save_to_file", "")
        switch_tab_id = args.get("switch_tab_id") or args.get("tab_id")
        no_monitor = args.get("no_monitor", False)
        result = web_execute_js(script, switch_tab_id=switch_tab_id, no_monitor=no_monitor)
        if save_to_file and "js_return" in result:
            content = str(result["js_return"] or '')
            abs_path = self._get_abs_path(save_to_file)
            result["js_return"] = smart_format(content, max_str_len=170)
            try:
                with open(abs_path, 'w', encoding='utf-8') as f: f.write(str(content))
                result["js_return"] += f"\n\n[已保存完整内容到 {abs_path}]"
            except: result['js_return'] += f"\n\n[保存失败，无法写入文件 {abs_path}]"
        show = smart_format(json.dumps(result, ensure_ascii=False, indent=2, default=json_default), max_str_len=300)
        self.print("Web Execute JS Result:", show)
        yield f"JS 执行结果:\n{show}\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        result = json.dumps(result, ensure_ascii=False, default=json_default)
        maxlen = 8000 // args.get('_tool_num', 1)
        return StepOutcome(smart_format(result, max_str_len=maxlen), next_prompt=next_prompt)
    
    def do_file_patch(self, args, response):
        path = self._get_abs_path(args.get("path", ""))
        yield f"[Action] Patching file: {path}\n"
        old_content = args.get("old_content", "")
        new_content = args.get("new_content", "")
        try: new_content = expand_file_refs(new_content, base_dir=self.cwd)
        except ValueError as e:
            yield f"[Status] ❌ 引用展开失败: {e}\n"
            return StepOutcome({"status": "error", "msg": str(e)}, next_prompt="\n")
        result = file_patch(path, old_content, new_content)
        yield f"\n{str(result)}\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome(result, next_prompt=next_prompt)
    
    def do_file_write(self, args, response):
        '''用于对整个文件的大量处理，精细修改要用file_patch。
        需要将要写入的内容放在<file_content>标签内，或者放在代码块中'''
        path = self._get_abs_path(args.get("path", ""))
        mode = args.get("mode", "overwrite")  # overwrite/append/prepend
        action_str = {"prepend": "Prepending to", "append": "Appending to"}.get(mode, "Overwriting")
        yield f"[Action] {action_str} file: {os.path.basename(path)}\n"

        def extract_robust_content(text):
            tags = re.findall(r"<file_content[^>]*>(.*?)</file_content>", text, re.DOTALL)
            if tags: return tags[-1].strip()
            blocks = re.findall(r"```[^\n]*\n([\s\S]*?)```", text)
            if blocks: return blocks[-1].strip()
            return None
        
        content = args.get('content') or extract_robust_content(response.content)
        if not content:
            yield f"[Status] ❌ 失败: 未在回复中找到<file_content>代码块内容\n"
            return StepOutcome({"status": "error", "msg": "No content found. Blank is not supported. Put content inside <file_content>...</file_content> tags in your reply body before call file_write."}, next_prompt="\n")
        try:
            new_content = expand_file_refs(content, base_dir=self.cwd)
            if mode == "prepend":
                old = open(path, 'r', encoding="utf-8").read() if os.path.exists(path) else ""
                open(path, 'w', encoding="utf-8").write(new_content + old)
            else:
                with open(path, 'a' if mode == "append" else 'w', encoding="utf-8") as f: f.write(new_content)
            yield f"[Status] ✅ {mode.capitalize()} 成功 ({len(new_content)} bytes)\n"
            next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
            return StepOutcome({"status": "success", 'writed_bytes': len(new_content)}, next_prompt=next_prompt)
        except Exception as e:
            yield f"[Status] ❌ 写入异常: {str(e)}\n"
            return StepOutcome({"status": "error", "msg": str(e)}, next_prompt="\n")
        
    def do_file_read(self, args, response):
        '''读取文件内容。从第start行开始读取。如有keyword则返回第一个keyword(忽略大小写)周边内容'''
        path = self._get_abs_path(args.get("path", ""))
        yield f"\n[Action] Reading file: {path}\n"
        start = args.get("start", 1)
        count = args.get("count", 200)
        keyword = args.get("keyword")
        show_linenos = args.get("show_linenos", True)
        result = file_read(path, start=start, keyword=keyword,
                           count=count, show_linenos=show_linenos)
        if show_linenos and not result.startswith("Error:"): result = '由于设置了show_linenos，以下返回信息为：(行号|)内容 。\n' + result 
        if ' ... [TRUNCATED]' in result: result += '\n\n（某些行被截断，如需完整内容可改用 code_run 读取）'
        maxlen = 15000 // args.get('_tool_num', 1)
        result = smart_format(result, max_str_len=maxlen, omit_str='\n\n[omitted long content]\n\n')
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        log_memory_access(path)
        if 'memory' in path or 'sop' in path: 
            next_prompt += "\n[SYSTEM TIPS] 正在读取记忆或SOP文件，若决定按sop执行请提取sop中的关键点（特别是靠后的）update working memory."
        return StepOutcome(result, next_prompt=next_prompt)
    
    def export_history(self, fn): 
        with open(fn, 'w', encoding='utf-8') as f: json.dump(self.parent.llmclient.backend.history, f, ensure_ascii=False)
    def _in_plan_mode(self): return self.working.get('in_plan_mode')
    def _exit_plan_mode(self): self.working.pop('in_plan_mode', None)
    def enter_plan_mode(self, plan_path): 
        self.working['in_plan_mode'] = plan_path; self.max_turns = 100
        self.print(f"[Info] Entered plan mode with plan file: {plan_path}")
        return plan_path
    def _check_plan_completion(self):
        if not os.path.isfile(p:=self._in_plan_mode() or ''): return None
        try: return len(re.findall(r'\[ \]', open(p, encoding='utf-8', errors='replace').read()))
        except: return None
    
    def do_update_working_checkpoint(self, args, response):
        '''为整个任务设定后续需要临时记忆的重点。'''
        key_info = args.get("key_info", "")
        related_sop = args.get("related_sop", "")
        if "key_info" in args: self.working['key_info'] = key_info
        if "related_sop" in args: self.working['related_sop'] = related_sop
        self.working['passed_sessions'] = 0
        yield f"[Info] Updated key_info and related_sop.\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        #next_prompt += '\n[SYSTEM TIPS] 此函数一般在任务开始或中间时调用，如果任务已成功完成应该是start_long_term_update用于结算长期记忆。\n'
        return StepOutcome({"result": "working key_info updated"}, next_prompt=next_prompt)

    def do_get_skill_detail(self, args, response):
        '''懒加载某个 skill 的详情（progressive disclosure）。返回 frontmatter 摘要 + 资源目录概览 + SKILL.md 路径。'''
        name = args.get('name', '')
        yield f"\n[Action] get_skill_detail: {name}\n"
        try:
            detail = get_skill_detail(name)
        except Exception as e:
            yield f"[Error] get_skill_detail failed: {e}\n"
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt="\n")
        yield f"[Skill Detail] {json.dumps(detail, ensure_ascii=False, indent=2)}\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        if detail.get('status') != 'error':
            next_prompt += "\n[SYSTEM TIPS] 已返回 skill 摘要与 SKILL.md 路径。如需完整内容请用 file_read 读取 skill_md_path。"
        return StepOutcome(detail, next_prompt=next_prompt)

    def do_mcp_call(self, args, response):
        '''调用连接的 MCP (Model Context Protocol) Server 上的工具。通过 get_system_prompt 的 MCP 部分发现可用的 server 和工具。'''
        server = args.get('server', '')
        tool = args.get('tool', '')
        arguments = args.get('arguments', {})
        mcp_mgr = getattr(self, '_mcp_manager', None)
        if mcp_mgr is None:
            yield f"[Error] MCP Client not initialized\n"
            return StepOutcome(None, next_prompt="MCP Client not available")
        try:
            result = mcp_mgr.call_tool(server, tool, arguments)
            yield f"[MCP] {server}/{tool} result:\n{json.dumps(result, indent=2, ensure_ascii=False, default=json_default)}\n"
            return StepOutcome(result, next_prompt="\n")
        except Exception as e:
            yield f"[MCP Error] {server}/{tool}: {e}\n"
            return StepOutcome(None, next_prompt=f"MCP call failed: {e}")

    def _retry_or_exit(self, prompt):
        self._empty_ct = getattr(self, '_empty_ct', 0) + 1
        if self._empty_ct >= 3: return StepOutcome({}, should_exit=True)
        return StepOutcome({}, next_prompt=prompt)

    def do_no_tool(self, args, response):
        '''这是一个特殊工具，由引擎自主调用，不要包含在TOOLS_SCHEMA里。
        当模型在一轮中未显式调用任何工具时，由引擎自动触发。
        二次确认仅在回复几乎只包含<thinking>/<summary>和一段大代码块时触发。'''
        content = getattr(response, 'content', '') or ""
        thinking = getattr(response, 'thinking', '') or ""
        if not response or (not content.strip() and not thinking.strip()):
            yield "[Warn] LLM returned an empty response. Retrying...\n"
            return self._retry_or_exit("[System] Blank response, regenerate and tooluse")
        if '[!!! 流异常中断' in content[-100:] or '!!!Error:' in content[-100:]:
            return self._retry_or_exit("[System] Incomplete response. Regenerate and tooluse.")
        if 'max_tokens !!!]' in content[-100:]:
            return self._retry_or_exit("[System] max_tokens limit reached. Use multi small steps to do it.")
        
        if self._in_plan_mode() and any(kw in content for kw in ['任务完成', '全部完成', '已完成所有', '🏁']):
            if 'VERDICT' not in content and '[VERIFY]' not in content and '验证subagent' not in content:
                yield "[Warn] Plan模式完成声明拦截。\n"
                return StepOutcome({}, next_prompt="⛔ [验证拦截] 检测到你在plan模式下声称完成，但未执行[VERIFY]验证步骤。请先按plan_sop §四启动验证subagent，获得VERDICT后才能声称完成。")
            
        # 2. 检测"包含较大代码块但未调用工具"的情况
        # 关键特征：恰好1个大代码块 + 代码块直接结尾（后面只有空白）
        code_block_pattern = r"```[a-zA-Z0-9_]*\n[\s\S]{50,}?```"
        blocks = re.findall(code_block_pattern, content)
        if len(blocks) == 1:
            m = re.search(code_block_pattern, content)
            after_block = content[m.end():]
            if not after_block.strip():
                residual = content.replace(m.group(0), "")
                residual = re.sub(r"<thinking>[\s\S]*?</thinking>", "", residual, flags=re.IGNORECASE)
                residual = re.sub(r"<summary>[\s\S]*?</summary>", "", residual, flags=re.IGNORECASE)
                clean_residual = re.sub(r"\s+", "", residual)
                if len(clean_residual) <= 30:
                    yield "[Info] Detected large code block without tool call and no extra natural language. Requesting clarification.\n"
                    next_prompt = (
                        "[System] 检测到你在上一轮回复中主要内容是较大代码块，且本轮未调用任何工具。\n"
                        "如果这些代码需要执行、写入文件或进一步分析，请重新组织回复并显式调用相应工具"
                        "（例如：code_run、file_write、file_patch 等）；\n"
                        "如果只是向用户展示或讲解代码片段，请在回复中补充自然语言说明，"
                        "并明确是否还需要额外的实际操作。"
                    )
                    return StepOutcome({}, next_prompt=next_prompt)
                
        if self._in_plan_mode():
            remaining = self._check_plan_completion()
            if remaining == 0:
                self._exit_plan_mode(); yield "[Info] Plan完成：plan.md中0个[ ]残留，退出plan模式。\n"
        
        #yield "[Info] Final response to user.\n"
        return StepOutcome(response, next_prompt=None)
    
    def do_start_long_term_update(self, args, response):
        '''Agent觉得当前任务完成后有重要信息需要记忆时调用此工具。'''
        prompt = '''### [总结提炼经验] 既然你觉得当前任务有重要信息需要记忆，请提取最近一次任务中【事实验证成功且长期有效】的环境事实、用户偏好、重要步骤，更新记忆。
本工具是标记开启结算过程，若已在更新记忆过程或没有值得记忆的点，忽略本次调用。
**如果没有经验证的，未来能用上的信息，忽略本次调用！**
**只能提取行动验证成功的信息**：
- **环境事实**（路径/凭证/配置）→ `file_patch` 更新 L2，同步 L1
- **复杂任务经验**（关键坑点/前置条件/重要步骤）→ L3 精简 SOP（只记你被坑得多次重试的核心要点）
- **MCP 依赖标注**：若任务中调用了 mcp_call，先 `file_read memory/mcp_call_log.md` 查本次 server/tool，在生成的 SOP/Skill frontmatter 加 `mcp_dependencies: server/tool`
**禁止**：临时变量、具体推理过程、未验证信息、通用常识、你可以轻松复现的细节、只是做了但没有验证的信息
**操作**：严格遵循提供的L0的记忆更新SOP。先 `file_read` 看现有 → 判断类型 → 最小化更新 → 无新内容跳过，保证对记忆库最小局部修改。\n
''' + get_global_memory()
        yield "[Info] Start distilling good memory for long-term storage.\n"
        path = './memory/memory_management_sop.md'
        if os.path.exists(path): result = 'This is L0:\n' + file_read(path, show_linenos=False)
        else: result = "Memory Management SOP not found. Do not update memory."
        # spec v1 手动入口：保留 L2/L3 更新语义，额外触发 skill 蒸馏（foreground origin, 后台 spawn）
        _client = getattr(self, 'client', None)
        if _client is not None and os.environ.get('GA_SKILL_EVOLUTION_ENABLED') == '1':
            try:
                import plugins.skill_evolution as _se
                def _manual():
                    tok = _se.skill_write_origin.set('foreground')
                    try:
                        _se.distill(_client, self, signal='manual')
                    finally:
                        _se.skill_write_origin.reset(tok)
                threading.Thread(target=_manual, daemon=True).start()
            except Exception:
                pass
        return StepOutcome(result, next_prompt=prompt)

    def _fold_earlier(self, lines):
        FALLBACK = '直接回答了用户问题'
        parts, cnt, last = [], 0, ''
        def flush():
            if cnt:
                if FALLBACK in last: parts.append(f'[Agent]（{cnt} turns）')
                else: parts.append(f'{last}（{cnt} turns）')
        for line in lines:
            if line.startswith('[USER]'):
                flush(); parts.append(line); cnt = 0; last = ''
            else: cnt += 1; last = line
        flush()
        return "\n".join(parts[-70:])

    def _get_anchor_prompt(self, skip=False):
        if skip: return "\n"
        h = self.history_info; W = 30
        earlier = f'<earlier_context>\n{self._fold_earlier(h[:-W])}\n</earlier_context>\n' if len(h) > W else ""
        h_str = "\n".join(h[-W:])
        prompt = f"\n### [WORKING MEMORY]\n{earlier}<history>\n{h_str}\n</history>"
        prompt += f"\nCurrent turn: {self.current_turn}\n"
        if self.working.get('key_info'): prompt += f"\n<key_info>{self.working.get('key_info')}</key_info>"
        if self.working.get('related_sop'): prompt += f"\n有不清晰的地方请再次读取{self.working.get('related_sop')}"
        if getattr(self.parent, 'verbose', False): self.print(prompt)
        return prompt
    
    def turn_end_callback(self, response, tool_calls, tool_results, turn, next_prompt, exit_reason):
        _c = re.sub(r'```.*?```|<thinking>.*?</thinking>', '', response.content, flags=re.DOTALL)
        rsumm = re.search(r"<summary>(.*?)</summary>", _c, re.DOTALL)
        if rsumm: summary = rsumm.group(1).strip()
        else:
            tc = tool_calls[0]; clean_args = {k: v for k, v in tc['args'].items() if not k.startswith('_')}   # at least one because no_tool
            summary = _c.strip() or smart_format("直接回答了用户问题" if tc['tool_name'] == 'no_tool' else f"{tc['tool_name']}, args: {clean_args}", max_str_len=40)
            next_prompt += "\n\n\n[SYSTEM] 必须在回复文本中包含<summary>！\n\n"
        summary = smart_format(summary.replace('\n', ''), max_str_len=80)
        self.history_info.append(f'[Agent] {summary}')
        _plan = self._in_plan_mode()

        if turn % 175 == 0 and (not _plan):
            next_prompt += f"\n\n[DANGER] Turn {turn}. Must call ask_user to summarize progress and get direction. No more blind retries."
        elif turn % 7 == 0:
            next_prompt += f"\n\n[SYSTEM] Turn {turn}. Call update_working_checkpoint to save key context. Stop ineffective retries; if no progress, switch strategy: 1) Probe physical boundaries 2) **Re-read relevant SOPs**"
        elif turn % 25 == 0:
            next_prompt += f"\n\n[SYSTEM] Turn {turn}. Write checkpoints/key findings/tried approaches to a **file** for future reference (not only working_checkpoint!). Avoid losing critical info."
        elif turn % 10 == 0: next_prompt += get_global_memory()

        if _plan and turn >= 10 and turn % 5 == 0:
            next_prompt = f"[Plan Hint] 正在计划模式。必须 file_read({_plan}) 确认当前步骤，回复开头引用：📌 当前步骤：...\n\n" + next_prompt
        if _plan and turn >= 190: next_prompt += f"\n\n[DANGER] Plan模式已运行 {turn} 轮，已达上限。必须 ask_user 汇报进度并确认是否继续。"

        injkeyinfo = self.parent.extrakeyinfo or consume_file(self.parent.task_dir, '_keyinfo')
        injprompt = self.parent.intervene or consume_file(self.parent.task_dir, '_intervene')
        if injkeyinfo: self.working['key_info'] = self.working.get('key_info', '') + f"\n[MASTER] {injkeyinfo}"
        if injprompt: next_prompt += f"\n\n[MASTER] {injprompt}\n"
        self.parent.intervene = self.parent.extrakeyinfo = None
        for hook in list(getattr(self.parent, '_turn_end_hooks', {}).values()): hook(locals())  # current readonly
        # self-evolution: flush queued skill-distillation briefs (batch at turn%10, force at task end)
        if self._pending_briefs and (turn % 10 == 0 or exit_reason):
            next_prompt += ''.join(self._pending_briefs)
            self._pending_briefs.clear()
        return next_prompt

def get_global_memory():
    prompt = "\n"
    try:
        try:
            sync_skills_to_l1()
        except Exception:
            pass
        suffix = '_en' if os.environ.get('GA_LANG', '') == 'en' else ''
        with open(os.path.join(script_dir, 'memory/global_mem_insight.txt'), 'r', encoding='utf-8', errors='replace') as f: insight = f.read()
        with open(os.path.join(script_dir, f'assets/insight_fixed_structure{suffix}.txt'), 'r', encoding='utf-8') as f: structure = f.read()
        prompt += f'cwd = {os.path.join(script_dir, "temp")} (./)\n'
        prompt += f"\n[Memory] (../memory)\n"
        prompt += structure + '\n../memory/global_mem_insight.txt:\n'
        prompt += insight + "\n"
    except FileNotFoundError: pass
    return prompt
