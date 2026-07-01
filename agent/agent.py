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

PF_CSS = "https://cdn.jsdelivr.net/npm/@patternfly/patternfly@6/patternfly.min.css"
PF_ADDONS = "https://cdn.jsdelivr.net/npm/@patternfly/patternfly@6/patternfly-addons.css"

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
    "last_actions": [],
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
    <link rel="stylesheet" href="{{ pf_css }}">
    <style>
        body { background: var(--pf-t--global--background--color--primary); }
        .todo-wrap { max-width: 600px; margin: 0 auto; padding: var(--pf-t--global--spacer--xl); }
        .todo-item { display: flex; align-items: center; gap: var(--pf-t--global--spacer--sm);
                     padding: var(--pf-t--global--spacer--sm) 0;
                     border-bottom: 1px solid var(--pf-t--global--border--color--default); }
        .todo-item.completed .todo-text { text-decoration: line-through;
                                          color: var(--pf-t--global--text--color--subtle); }
        .todo-text { flex: 1; }
        .add-row { display: flex; gap: var(--pf-t--global--spacer--sm); margin-bottom: var(--pf-t--global--spacer--lg); }
        .add-row input { flex: 1; }
    </style>
</head>
<body>
    <div class="todo-wrap">
        <h1 class="pf-v6-c-title pf-m-2xl" style="margin-bottom:var(--pf-t--global--spacer--lg)">TODO App</h1>
        <form class="add-row" action="/app/add" method="post">
            <input class="pf-v6-c-form-control" type="text" name="task"
                   placeholder="Enter a new task..." required aria-label="New task">
            <button class="pf-v6-c-button pf-m-primary" type="submit">Add</button>
        </form>
        <div>
            {% for todo in todos %}
            <div class="todo-item {{ 'completed' if todo.done else '' }}">
                <form action="/app/toggle/{{ todo.id }}" method="post" style="display:inline">
                    <button class="pf-v6-c-button pf-m-plain" type="submit"
                            aria-label="{{ 'Mark incomplete' if todo.done else 'Mark complete' }}: {{ todo.task }}">
                        {% if todo.done %}&#9745;{% else %}&#9744;{% endif %}
                    </button>
                </form>
                <span class="todo-text">{{ todo.task }}</span>
                <form action="/app/delete/{{ todo.id }}" method="post" style="display:inline">
                    <button class="pf-v6-c-button pf-m-plain pf-m-danger" type="submit"
                            aria-label="Delete: {{ todo.task }}">&#x2715;</button>
                </form>
            </div>
            {% endfor %}
        </div>
        <p style="margin-top:var(--pf-t--global--spacer--md);color:var(--pf-t--global--text--color--subtle)">
            {{ todos | rejectattr('done') | list | length }} item(s) remaining
        </p>
    </div>
</body>
</html>"""

# -- Dashboard -------------------------------------------------------------

DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en" class="pf-v6-theme-dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Browser Testing Dashboard</title>
    <link rel="stylesheet" href="{{ pf_css }}">
    <link rel="stylesheet" href="{{ pf_addons }}">
    <style>
        body { background: var(--pf-t--global--background--color--primary); }
        .dash-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--pf-t--global--spacer--lg);
                     padding: var(--pf-t--global--spacer--lg); }
        @media (max-width: 992px) { .dash-grid { grid-template-columns: 1fr; } }
        .link-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--pf-t--global--spacer--md);
                     margin-bottom: var(--pf-t--global--spacer--lg); }
        .step-row { display: flex; align-items: center; gap: var(--pf-t--global--spacer--sm);
                    padding: var(--pf-t--global--spacer--sm) var(--pf-t--global--spacer--md);
                    border-bottom: 1px solid var(--pf-t--global--border--color--default); }
        .step-name { flex: 1; }
        .hist-row { display: flex; align-items: center; gap: var(--pf-t--global--spacer--md);
                    padding: var(--pf-t--global--spacer--sm) 0;
                    border-bottom: 1px solid var(--pf-t--global--border--color--default); font-size: 14px; }
        .hist-row .hist-time { margin-left: auto; color: var(--pf-t--global--text--color--subtle); font-size: 12px; }
        .log-line { font-family: var(--pf-t--global--font--family--mono); font-size: 12px;
                    color: var(--pf-t--global--text--color--subtle); padding: 2px 0;
                    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }
        .pulse { animation: pulse 1.5s infinite; }
    </style>
</head>
<body>
    <header class="pf-v6-c-masthead">
        <div class="pf-v6-c-masthead__main" style="padding:var(--pf-t--global--spacer--md) var(--pf-t--global--spacer--lg)">
            <span class="pf-v6-c-title pf-m-lg" style="color:var(--pf-t--global--text--color--on-brand--default)">AI Browser Testing</span>
            <span style="margin-left:var(--pf-t--global--spacer--md);font-size:13px;color:var(--pf-t--global--text--color--subtle)">
                Playwright MCP + Qwen3 8B on Red Hat OpenShift AI
            </span>
        </div>
    </header>

    <div style="padding:var(--pf-t--global--spacer--sm) var(--pf-t--global--spacer--lg);display:flex;align-items:center;gap:var(--pf-t--global--spacer--sm);border-bottom:1px solid var(--pf-t--global--border--color--default)">
        <span class="pf-v6-c-label pf-m-compact" id="statusLabel">
            <span class="pf-v6-c-label__content"><span class="pf-v6-c-label__text" id="statusText">Starting...</span></span>
        </span>
        <span style="font-size:13px;color:var(--pf-t--global--text--color--subtle)" id="statusDetail"></span>
    </div>

    <div class="dash-grid">
        <div>
            <div class="link-grid">
                <a id="vncLink" href="#" target="_blank" class="pf-v6-c-card pf-m-clickable" style="text-decoration:none">
                    <div class="pf-v6-c-card__header"><div class="pf-v6-c-card__title"><span class="pf-v6-c-card__title-text">Watch the AI</span></div></div>
                    <div class="pf-v6-c-card__body" style="font-size:13px;color:var(--pf-t--global--text--color--subtle)">Live browser view via noVNC</div>
                </a>
                <a href="/app" target="_blank" class="pf-v6-c-card pf-m-clickable" style="text-decoration:none">
                    <div class="pf-v6-c-card__header"><div class="pf-v6-c-card__title"><span class="pf-v6-c-card__title-text">TODO App</span></div></div>
                    <div class="pf-v6-c-card__body" style="font-size:13px;color:var(--pf-t--global--text--color--subtle)">The application under test</div>
                </a>
            </div>

            <div class="pf-v6-c-card">
                <div class="pf-v6-c-card__header"><div class="pf-v6-c-card__title"><span class="pf-v6-c-card__title-text">Current Run</span></div></div>
                <div class="pf-v6-c-card__body" id="currentRun">
                    <p style="text-align:center;padding:var(--pf-t--global--spacer--xl);color:var(--pf-t--global--text--color--subtle)">Waiting for first test run...</p>
                </div>
            </div>

            <div class="pf-v6-c-card" style="margin-top:var(--pf-t--global--spacer--md)" id="actionCard" hidden>
                <div class="pf-v6-c-card__header"><div class="pf-v6-c-card__title"><span class="pf-v6-c-card__title-text">Recent Actions</span></div></div>
                <div class="pf-v6-c-card__body" id="actionLog"></div>
            </div>
        </div>

        <div>
            <div class="pf-v6-c-card pf-m-full-height">
                <div class="pf-v6-c-card__header"><div class="pf-v6-c-card__title"><span class="pf-v6-c-card__title-text">Test History</span></div></div>
                <div class="pf-v6-c-card__body" id="history">
                    <p style="text-align:center;padding:var(--pf-t--global--spacer--xl);color:var(--pf-t--global--text--color--subtle)">No completed runs yet</p>
                </div>
            </div>
        </div>
    </div>

    <script>
    const STEPS = ['Navigate & verify heading','Add "Buy groceries"','Add "Write report"','Toggle complete','Delete "Write report"'];

    function updateDashboard() {
        fetch('/api/results').then(r => r.json()).then(data => {
            const sLabel = document.getElementById('statusLabel');
            const sText = document.getElementById('statusText');
            const sDetail = document.getElementById('statusDetail');
            const colors = {starting:'',running:'pf-m-green',idle:'pf-m-orange',waiting:'pf-m-orange'};
            const labels = {starting:'Starting',running:'Running',idle:'Idle',waiting:'Waiting'};
            sLabel.className = 'pf-v6-c-label pf-m-compact ' + (colors[data.status]||'');
            sText.textContent = labels[data.status] || data.status;
            if (data.status === 'running') sText.classList.add('pulse');
            else sText.classList.remove('pulse');
            sDetail.textContent = data.status === 'running'
                ? 'Run #' + data.run_number + ' • Iteration ' + data.iteration + '/' + data.max_iterations
                : data.runs.length + ' runs completed';

            const cur = document.getElementById('currentRun');
            if (data.run_number > 0) {
                let h = '<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px"><b>Run #' + data.run_number + '</b>';
                if (data.status === 'running') h += ' <span class="pf-v6-c-label pf-m-compact pf-m-blue"><span class="pf-v6-c-label__content"><span class="pf-v6-c-label__text">Running</span></span></span>';
                h += '</div>';
                for (let i = 0; i < 5; i++) {
                    const s = data.steps[i];
                    let icon = '•', cls = 'color:var(--pf-t--global--text--color--subtle)', detail = '';
                    if (s) {
                        if (s.result === 'PASS') { icon = '✔'; cls = 'color:var(--pf-t--global--color--status--success--default)'; detail = s.detail||''; }
                        else if (s.result === 'FAIL') { icon = '✘'; cls = 'color:var(--pf-t--global--color--status--danger--default)'; detail = s.detail||''; }
                    } else if (data.status === 'running') { icon = '▶'; cls = 'color:var(--pf-t--global--color--status--info--default)'; }
                    h += '<div class="step-row"><span style="' + cls + '">' + icon + '</span><span class="step-name">Step ' + (i+1) + ': ' + STEPS[i] + '</span><span style="font-size:12px;color:var(--pf-t--global--text--color--subtle)">' + detail + '</span></div>';
                }
                cur.innerHTML = h;
            }

            const ac = document.getElementById('actionCard');
            const al = document.getElementById('actionLog');
            if (data.last_actions && data.last_actions.length > 0) {
                ac.hidden = false;
                al.innerHTML = data.last_actions.slice(-8).map(a =>
                    '<div class="log-line"><span style="color:var(--pf-t--global--color--status--purple--default)">' + a.tool + '</span> ' +
                    '<span style="color:var(--pf-t--global--color--status--success--default)">' + (a.args||'') + '</span></div>'
                ).join('');
            }

            const hi = document.getElementById('history');
            if (data.runs.length > 0) {
                hi.innerHTML = data.runs.slice().reverse().map(run => {
                    const p = run.steps.filter(s => s.result === 'PASS').length;
                    const t = run.steps.length || 5;
                    const c = p === t ? 'pf-m-green' : (p > 0 ? 'pf-m-orange' : 'pf-m-red');
                    return '<div class="hist-row"><span>Run #' + run.number + '</span>' +
                        '<span class="pf-v6-c-label pf-m-compact ' + c + '"><span class="pf-v6-c-label__content"><span class="pf-v6-c-label__text">' + p + '/' + t + '</span></span></span>' +
                        run.steps.map(s => '<span style="font-size:14px">' + (s.result === 'PASS' ? '✔' : '✘') + '</span>').join('') +
                        '<span class="hist-time">' + (run.finished||'') + '</span></div>';
                }).join('');
            }
        }).catch(() => {});
    }

    const h = window.location.hostname;
    document.getElementById('vncLink').href = window.location.protocol + '//' + h.replace(/^dashboard-/, 'browser-live-view-') + '/vnc.html';
    setInterval(updateDashboard, 2000);
    updateDashboard();
    </script>
</body>
</html>"""


@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_TEMPLATE, pf_css=PF_CSS, pf_addons=PF_ADDONS)


@app.route("/app")
def todo_index():
    return render_template_string(TODO_TEMPLATE, todos=todos, pf_css=PF_CSS)


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
