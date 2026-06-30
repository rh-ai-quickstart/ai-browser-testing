import asyncio
import json
import os
import re
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone

from flask import Flask, request, redirect, url_for, render_template_string, jsonify
from openai import AsyncOpenAI
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp import ClientSession

MODEL_ENDPOINT = os.environ.get("MODEL_ENDPOINT", "http://localhost:8080/v1")
MODEL_NAME = os.environ.get("MODEL_NAME", "qwen3-8b")
APP_PORT = int(os.environ.get("APP_PORT", "5000"))
TODO_APP_URL = f"http://localhost:{APP_PORT}"
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "30"))
MAX_TOOL_RESULT_CHARS = 4000
VNC_PATH = os.environ.get("VNC_PATH", "/vnc.html")

# ---------------------------------------------------------------------------
# Shared test state — written by agent, read by dashboard
# ---------------------------------------------------------------------------

test_state = {
    "status": "starting",
    "run_number": 0,
    "current_step": None,
    "iteration": 0,
    "max_iterations": MAX_ITERATIONS,
    "steps": [],
    "runs": [],
    "last_action": None,
}

# ---------------------------------------------------------------------------
# Flask app — dashboard + TODO app
# ---------------------------------------------------------------------------

app = Flask(__name__)
todos = []

# -- TODO App (test target) ------------------------------------------------

TODO_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TODO App</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 0 20px; }
        h1 { margin-bottom: 20px; }
        form.add-form { display: flex; gap: 8px; margin-bottom: 24px; }
        form.add-form input[type="text"] { flex: 1; padding: 8px; font-size: 16px; }
        button { padding: 8px 16px; font-size: 14px; cursor: pointer; }
        ul { list-style: none; padding: 0; }
        li { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid #eee; }
        li.completed span.todo-text { text-decoration: line-through; color: #888; }
        span.todo-text { flex: 1; }
        .status { color: #666; margin-top: 16px; }
    </style>
</head>
<body>
    <h1>TODO App</h1>
    <form class="add-form" action="/app/add" method="post">
        <label for="todo-input" class="sr-only">New task</label>
        <input type="text" id="todo-input" name="task" placeholder="Enter a new task..." required
               aria-label="New task">
        <button type="submit">Add</button>
    </form>
    <ul aria-label="Task list">
        {% for todo in todos %}
        <li class="{{ 'completed' if todo.done else '' }}">
            <form action="/app/toggle/{{ todo.id }}" method="post" style="display:inline">
                <button type="submit"
                        aria-label="{{ 'Mark incomplete' if todo.done else 'Mark complete' }}: {{ todo.task }}">
                    {{ '☑' if todo.done else '☐' }}
                </button>
            </form>
            <span class="todo-text">{{ todo.task }}</span>
            <form action="/app/delete/{{ todo.id }}" method="post" style="display:inline">
                <button type="submit" aria-label="Delete: {{ todo.task }}">&#x2715;</button>
            </form>
        </li>
        {% endfor %}
    </ul>
    <p class="status">{{ todos | rejectattr('done') | list | length }} item(s) remaining</p>
</body>
</html>"""


@app.route("/app")
def todo_index():
    return render_template_string(TODO_TEMPLATE, todos=todos)


@app.route("/app/add", methods=["POST"])
def todo_add():
    task = request.form.get("task", "").strip()
    if task:
        todos.append({"id": str(uuid.uuid4())[:8], "task": task, "done": False})
    return redirect(url_for("todo_index"))


@app.route("/app/toggle/<todo_id>", methods=["POST"])
def todo_toggle(todo_id):
    for todo in todos:
        if todo["id"] == todo_id:
            todo["done"] = not todo["done"]
            break
    return redirect(url_for("todo_index"))


@app.route("/app/delete/<todo_id>", methods=["POST"])
def todo_delete(todo_id):
    global todos
    todos = [t for t in todos if t["id"] != todo_id]
    return redirect(url_for("todo_index"))


# -- Dashboard -------------------------------------------------------------

DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Browser Testing Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Inter', -apple-system, system-ui, sans-serif; background: #0f1117; color: #e1e4e8; min-height: 100vh; }

        .header { background: linear-gradient(135deg, #1a1c24 0%, #252830 100%); border-bottom: 1px solid #2d3139; padding: 24px 32px; }
        .header h1 { font-size: 22px; font-weight: 600; color: #fff; }
        .header p { font-size: 13px; color: #8b949e; margin-top: 4px; }

        .status-bar { display: flex; align-items: center; gap: 12px; padding: 16px 32px; background: #161920; border-bottom: 1px solid #2d3139; }
        .status-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
        .status-dot.running { background: #3fb950; animation: pulse 1.5s infinite; }
        .status-dot.idle { background: #f0883e; }
        .status-dot.starting { background: #8b949e; animation: pulse 1s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
        .status-text { font-size: 14px; font-weight: 500; }
        .status-detail { font-size: 13px; color: #8b949e; margin-left: auto; }

        .main { display: grid; grid-template-columns: 1fr 1fr; gap: 0; min-height: calc(100vh - 130px); }
        @media (max-width: 900px) { .main { grid-template-columns: 1fr; } }

        .panel { padding: 24px 32px; }
        .panel-left { border-right: 1px solid #2d3139; }

        .section-title { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.2px; color: #8b949e; margin-bottom: 16px; }

        .links { display: flex; gap: 12px; margin-bottom: 32px; }
        .link-card { flex: 1; padding: 16px; border-radius: 8px; border: 1px solid #2d3139; background: #1a1c24; text-decoration: none; color: #e1e4e8; transition: border-color 0.2s, background 0.2s; }
        .link-card:hover { border-color: #58a6ff; background: #1c2333; }
        .link-card .link-label { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
        .link-card .link-desc { font-size: 12px; color: #8b949e; }

        .run-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid #2d3139; }
        .run-number { font-size: 18px; font-weight: 600; }
        .run-badge { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; text-transform: uppercase; }
        .run-badge.pass { background: #1b3a2d; color: #3fb950; }
        .run-badge.fail { background: #3d1f20; color: #f85149; }
        .run-badge.running { background: #1c2333; color: #58a6ff; }

        .steps { display: flex; flex-direction: column; gap: 8px; }
        .step { display: flex; align-items: center; gap: 12px; padding: 10px 14px; border-radius: 6px; background: #1a1c24; border: 1px solid #2d3139; font-size: 13px; }
        .step.pass { border-left: 3px solid #3fb950; }
        .step.fail { border-left: 3px solid #f85149; }
        .step.active { border-left: 3px solid #58a6ff; background: #1c2333; }
        .step.pending { border-left: 3px solid #30363d; opacity: 0.5; }
        .step-icon { font-size: 16px; flex-shrink: 0; }
        .step-name { font-weight: 500; flex: 1; }
        .step-result { font-size: 12px; color: #8b949e; }

        .history { margin-top: 24px; }
        .history-item { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid #1a1c24; font-size: 13px; }
        .history-score { font-weight: 600; min-width: 40px; }
        .history-score.perfect { color: #3fb950; }
        .history-score.partial { color: #f0883e; }
        .history-score.zero { color: #f85149; }
        .history-time { color: #8b949e; margin-left: auto; font-size: 12px; }

        .action-log { margin-top: 24px; }
        .log-entry { font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 12px; color: #8b949e; padding: 3px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .log-entry .tool { color: #d2a8ff; }
        .log-entry .args { color: #7ee787; }

        .empty-state { text-align: center; padding: 48px 24px; color: #484f58; }
        .empty-state p { font-size: 14px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>AI Browser Testing Dashboard</h1>
        <p>Autonomous QA testing powered by Qwen3 8B + Playwright MCP on Red Hat OpenShift AI</p>
    </div>

    <div class="status-bar" id="statusBar">
        <div class="status-dot starting" id="statusDot"></div>
        <span class="status-text" id="statusText">Connecting...</span>
        <span class="status-detail" id="statusDetail"></span>
    </div>

    <div class="main">
        <div class="panel panel-left">
            <div class="section-title">Live Views</div>
            <div class="links">
                <a class="link-card" id="vncLink" href="#" target="_blank">
                    <div class="link-label">Watch the AI</div>
                    <div class="link-desc">Live browser view via noVNC</div>
                </a>
                <a class="link-card" href="/app" target="_blank">
                    <div class="link-label">TODO App</div>
                    <div class="link-desc">The application under test</div>
                </a>
            </div>

            <div class="section-title">Current Run</div>
            <div id="currentRun">
                <div class="empty-state"><p>Waiting for first test run...</p></div>
            </div>

            <div class="action-log" id="actionLog"></div>
        </div>

        <div class="panel">
            <div class="section-title">Test History</div>
            <div id="history">
                <div class="empty-state"><p>No completed runs yet</p></div>
            </div>
        </div>
    </div>

    <script>
        function updateDashboard() {
            fetch('/api/results')
                .then(r => r.json())
                .then(data => {
                    // Status bar
                    const dot = document.getElementById('statusDot');
                    const text = document.getElementById('statusText');
                    const detail = document.getElementById('statusDetail');
                    dot.className = 'status-dot ' + data.status;
                    const labels = {starting: 'Starting up...', running: 'Test running', idle: 'Between runs', waiting: 'Waiting for next run'};
                    text.textContent = labels[data.status] || data.status;
                    if (data.status === 'running') {
                        detail.textContent = 'Run #' + data.run_number + ' • Iteration ' + data.iteration + '/' + data.max_iterations;
                    } else if (data.status === 'idle' || data.status === 'waiting') {
                        detail.textContent = data.runs.length + ' runs completed';
                    } else {
                        detail.textContent = '';
                    }

                    // Current run steps
                    const currentEl = document.getElementById('currentRun');
                    if (data.run_number > 0) {
                        let html = '<div class="run-header"><span class="run-number">Run #' + data.run_number + '</span>';
                        if (data.status === 'running') {
                            html += '<span class="run-badge running">Running</span>';
                        }
                        html += '</div><div class="steps">';
                        const stepDefs = ['Navigate & verify heading', 'Add "Buy groceries"', 'Add "Write report"', 'Toggle "Buy groceries"', 'Delete "Write report"'];
                        for (let i = 0; i < 5; i++) {
                            const step = data.steps[i];
                            let cls = 'pending', icon = '•', result = '';
                            if (step) {
                                if (step.result === 'PASS') { cls = 'pass'; icon = '✔'; result = step.detail || ''; }
                                else if (step.result === 'FAIL') { cls = 'fail'; icon = '✘'; result = step.detail || ''; }
                                else { cls = 'active'; icon = '▶'; result = 'In progress...'; }
                            } else if (data.status === 'running' && data.current_step === i + 1) {
                                cls = 'active'; icon = '▶'; result = 'In progress...';
                            }
                            html += '<div class="step ' + cls + '"><span class="step-icon">' + icon + '</span>';
                            html += '<span class="step-name">Step ' + (i+1) + ': ' + stepDefs[i] + '</span>';
                            html += '<span class="step-result">' + result + '</span></div>';
                        }
                        html += '</div>';
                        currentEl.innerHTML = html;
                    }

                    // Action log
                    const logEl = document.getElementById('actionLog');
                    if (data.last_actions && data.last_actions.length > 0) {
                        let html = '<div class="section-title" style="margin-top:24px">Recent Actions</div>';
                        data.last_actions.slice(-8).forEach(a => {
                            html += '<div class="log-entry"><span class="tool">' + a.tool + '</span> ';
                            html += '<span class="args">' + (a.args || '') + '</span></div>';
                        });
                        logEl.innerHTML = html;
                    }

                    // History
                    const histEl = document.getElementById('history');
                    if (data.runs.length > 0) {
                        let html = '';
                        data.runs.slice().reverse().forEach(run => {
                            const passed = run.steps.filter(s => s.result === 'PASS').length;
                            const total = run.steps.length || 5;
                            let cls = passed === total ? 'perfect' : (passed > 0 ? 'partial' : 'zero');
                            html += '<div class="history-item">';
                            html += '<span>Run #' + run.number + '</span>';
                            html += '<span class="history-score ' + cls + '">' + passed + '/' + total + '</span>';
                            run.steps.forEach(s => {
                                html += '<span style="font-size:14px">' + (s.result === 'PASS' ? '✔' : '✘') + '</span>';
                            });
                            html += '<span class="history-time">' + (run.finished || '') + '</span>';
                            html += '</div>';
                        });
                        histEl.innerHTML = html;
                    }
                })
                .catch(() => {});
        }
        // Construct VNC URL from dashboard hostname pattern
        const host = window.location.hostname;
        const vncHost = host.replace(/^todo-app-/, 'browser-live-view-');
        document.getElementById('vncLink').href = window.location.protocol + '//' + vncHost + '/vnc.html';

        setInterval(updateDashboard, 2000);
        updateDashboard();
    </script>
</body>
</html>"""


@app.route("/")
def dashboard():
    vnc_url = os.environ.get("VNC_URL", VNC_PATH)
    return render_template_string(DASHBOARD_TEMPLATE, vnc_url=vnc_url)


@app.route("/api/results")
def api_results():
    return jsonify(test_state)


@app.route("/health")
def health():
    return {"status": "ok"}


def start_app():
    import logging
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.run(host="0.0.0.0", port=APP_PORT)


# ---------------------------------------------------------------------------
# Testing Agent — bridges the LLM to Playwright MCP
# ---------------------------------------------------------------------------

ALLOWED_TOOLS = {
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_press_key",
    "browser_wait_for",
}

SYSTEM_PROMPT = """You are a QA testing agent that operates a web browser.

WORKFLOW - follow this exactly:
1. browser_navigate to go to a URL.
2. browser_snapshot to see the page. It returns YAML like:
     - textbox "New task" [ref=e5]
     - button "Add" [ref=e6]
3. To click an element, use its ref: browser_click(element="e6")
4. To type text, click the textbox first, then: browser_type(element="e5", text="hello")
5. browser_snapshot after every action to verify the result.

CRITICAL - READ THE SNAPSHOT CAREFULLY:
- Find elements by their TYPE: "textbox", "button", "link" - use THOSE refs.
- DO NOT use refs from "generic" or "heading" elements.
- Example: if snapshot shows generic [ref=e3] containing textbox [ref=e5] and button [ref=e6],
  use e5 for textbox and e6 for button. NOT e3.
- Report PASS only if the snapshot AFTER the action confirms the change happened."""

TASK_PROMPT = f"""Test the TODO application at {TODO_APP_URL}/app

Execute these test steps:

STEP 1: Navigate to {TODO_APP_URL}/app. Take a snapshot. Verify the heading "TODO App" is present.
STEP 2: Click the input field, type "Buy groceries", click the Add button. Take a snapshot. Verify the item appears.
STEP 3: Click the input field, type "Write report", click the Add button. Take a snapshot. Verify both items appear.
STEP 4: Click the toggle/mark-complete button next to "Buy groceries". Take a snapshot. Verify it shows as completed.
STEP 5: Click the delete button next to "Write report". Take a snapshot. Verify only "Buy groceries" remains.

After all steps, print EXACTLY this format (one line per step):
STEP 1: PASS/FAIL - description
STEP 2: PASS/FAIL - description
STEP 3: PASS/FAIL - description
STEP 4: PASS/FAIL - description
STEP 5: PASS/FAIL - description
OVERALL: X/5 passed"""


SNAPSHOT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def mcp_tools_to_openai(mcp_tools):
    tools = []
    for t in mcp_tools:
        if t.name not in ALLOWED_TOOLS:
            continue
        schema = t.inputSchema if t.inputSchema else {"type": "object", "properties": {}}
        if t.name == "browser_snapshot":
            schema = SNAPSHOT_SCHEMA
        tools.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": schema,
            },
        })
    return tools


def extract_text(mcp_result):
    parts = []
    for block in mcp_result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    text = "\n".join(parts)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + "\n... (truncated)"
    return text


def parse_step_results(text):
    results = []
    for m in re.finditer(r"STEP\s+(\d+)\s*:\s*(PASS|FAIL)\s*-?\s*(.*)", text):
        results.append({
            "step": int(m.group(1)),
            "result": m.group(2),
            "detail": m.group(3).strip(),
        })
    return results


async def run_agent():
    test_state["status"] = "running"
    test_state["steps"] = []
    test_state["last_actions"] = []
    test_state["iteration"] = 0

    print(f"Model: {MODEL_ENDPOINT} ({MODEL_NAME})")
    print(f"Target: {TODO_APP_URL}/app")
    print("---")

    client = AsyncOpenAI(base_url=MODEL_ENDPOINT, api_key="not-needed")

    chrome_wrapper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome-wrapper.sh")
    mcp_args = [
        "/opt/playwright-mcp/node_modules/@playwright/mcp/cli.js",
        "--no-sandbox",
        "--image-responses", "omit",
        "--executable-path", chrome_wrapper,
    ]

    server_params = StdioServerParameters(
        command="node",
        args=mcp_args,
        env={**os.environ},
    )

    async with stdio_client(server_params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            openai_tools = mcp_tools_to_openai(tools_result.tools)
            tool_names = [t["function"]["name"] for t in openai_tools]
            print(f"Tools: {tool_names}")
            print("---")

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": TASK_PROMPT},
            ]

            for iteration in range(MAX_ITERATIONS):
                test_state["iteration"] = iteration + 1
                print(f"\n[Iteration {iteration + 1}/{MAX_ITERATIONS}]")

                response = await client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=messages,
                    tools=openai_tools if openai_tools else None,
                    tool_choice="auto",
                    temperature=0,
                )

                choice = response.choices[0]

                if choice.message.content:
                    content = choice.message.content
                    if "<think>" in content:
                        content = content.split("</think>")[-1].strip()
                    if content:
                        print(f"Agent: {content}")
                        parsed = parse_step_results(content)
                        if parsed:
                            test_state["steps"] = parsed

                messages.append(choice.message.model_dump())

                if choice.finish_reason == "stop" or not choice.message.tool_calls:
                    print("\n--- Agent finished ---")
                    break

                for tool_call in choice.message.tool_calls:
                    name = tool_call.function.name
                    try:
                        args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        args = {}

                    print(f"  -> {name}({json.dumps(args, separators=(',', ':'))})")
                    test_state["last_actions"] = test_state.get("last_actions", [])[-15:] + [
                        {"tool": name, "args": json.dumps(args, separators=(",", ":"))}
                    ]

                    try:
                        result = await session.call_tool(name, args)
                        content = extract_text(result)
                    except Exception as e:
                        content = f"Error: {e}"

                    if name == "browser_snapshot":
                        print(f"  <- snapshot:\n{content[:2000]}")
                    elif "Error" in content or "error" in content.lower():
                        print(f"  <- {content[:500]}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": content,
                    })
            else:
                print("\n--- Max iterations reached ---")

    print("\nDone.")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting app on port %d..." % APP_PORT)
    app_thread = threading.Thread(target=start_app, daemon=True)
    app_thread.start()

    for _ in range(30):
        try:
            urllib.request.urlopen(f"{TODO_APP_URL}/health")
            break
        except Exception:
            time.sleep(0.1)
    else:
        print("ERROR: App failed to start")
        raise SystemExit(1)

    print("App ready. Dashboard at /  |  TODO app at /app")

    run_count = 0
    while True:
        run_count += 1
        todos.clear()
        test_state["run_number"] = run_count
        test_state["steps"] = []
        test_state["current_step"] = None

        print("\n" + "=" * 60)
        print(f"TEST RUN #{run_count}")
        print("=" * 60)

        asyncio.run(run_agent())

        run_record = {
            "number": run_count,
            "steps": list(test_state["steps"]),
            "finished": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        }
        test_state["runs"] = test_state.get("runs", [])[-19:] + [run_record]
        test_state["status"] = "waiting"

        print("\nNext run in 30 seconds...")
        time.sleep(30)
