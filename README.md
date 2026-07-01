# Automate Browser Testing with AI on OpenShift AI

Deploy an AI agent that autonomously tests web applications by operating a real browser, driven by natural-language instructions and an open-source LLM.

## Table of Contents

- [Detailed Description](#detailed-description)
  - [See It in Action](#see-it-in-action)
  - [Architecture](#architecture)
- [Requirements](#requirements)
  - [Hardware Requirements](#hardware-requirements)
  - [Software Requirements](#software-requirements)
- [Deploy](#deploy)
  - [Delete](#delete)
- [How It Works](#how-it-works)
- [Reference](#reference)
- [Tags](#tags)

## Detailed Description

Manual QA testing is time-consuming, repetitive, and struggles to keep pace with modern development cycles. This quickstart demonstrates how an open-source large language model can drive a real web browser to automatically test applications — replacing scripted test sequences with natural-language test instructions that an AI agent executes autonomously.

The quickstart deploys two components on Red Hat OpenShift AI: a Qwen3 8B model served via vLLM with tool-calling enabled, and a single container that bundles a testing dashboard, a simple TODO web application, and an AI testing agent. The agent connects the model to a browser through Playwright's Model Context Protocol (MCP), receives test steps in plain English, translates them into browser actions (navigate, click, type, verify), and reports pass/fail results. A dashboard ties everything together — watch the AI operate Chrome in real-time, browse the app under test, and track test results across runs.

This is a simplified demonstration of the concept. The 8B model handles straightforward test scenarios on simple UIs reliably, but production browser testing automation benefits from larger models or commercial APIs for complex multi-step workflows. The architecture and patterns shown here scale directly — swap in a more capable model and the same agent handles more sophisticated testing.

### See It in Action

After deploying, open the **dashboard URL** — it links to the live browser view (via noVNC) and the TODO app under test, and shows test results updating in real-time.

### Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                        OpenShift Cluster                          │
│                                                                   │
│  ┌──────────────────┐                                             │
│  │   RHOAI / vLLM   │  OpenAI-compatible API                      │
│  │   Qwen3 8B FP8   │◄────────────────────────┐                   │
│  │   (GPU)          │                         │                   │
│  └──────────────────┘                         │                   │
│                                               │                   │
│  ┌────────────────────────────────────────────┼──────────────┐    │
│  │           Browser Testing Agent            │              │    │
│  │                                            │              │    │
│  │  ┌─────────────┐    OpenAI SDK ───────────►│              │    │
│  │  │ Python Agent │                                         │    │
│  │  └──────┬──────┘                                          │    │
│  │         │ stdio (MCP protocol)                            │    │
│  │         ▼                                                 │    │
│  │  ┌────────────────┐      ┌────────────────────────┐       │    │
│  │  │ Playwright MCP │─────►│ Chrome on Xvfb         │       │    │
│  │  │ Server         │      │ (virtual display)      │       │    │
│  │  └────────────────┘      └───────────┬────────────┘       │    │
│  │                                      │                    │    │
│  │         ┌────────────────┐           │                    │    │
│  │         │  Dashboard  /  │───────────┤                    │    │
│  │         │  TODO App /app │◄──────────┘                    │    │
│  │         │  noVNC   :6080 │────────────────► Routes        │    │
│  │         └────────────────┘                                │    │
│  └───────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────┘
```

**Components:**

1. **Qwen3 8B FP8** — Open-source LLM served via vLLM on Red Hat OpenShift AI with tool-calling enabled. Receives browser snapshots and decides what to click, type, or verify next.
2. **Browser Testing Agent** — A single container running:
   - **Dashboard** — Real-time test results, links to live browser view and app under test
   - **TODO App** (Flask) — A simple web application used as the test target
   - **Playwright MCP Server** — Exposes browser automation as tool calls over the Model Context Protocol
   - **Chrome on Xvfb** — A real browser rendering on a virtual display, streamed via noVNC

## Requirements

### Hardware Requirements

- **GPU:** 1x NVIDIA GPU with at least 24 GB VRAM
  - A10G (24 GB), L40S (48 GB), or A100 (40/80 GB)
  - FP8 quantization requires compute capability 8.0+ (Ampere or newer). T4 GPUs are not compatible.
- **CPU:** 4 vCPU total (1 for model serving + 2 for agent/browser + overhead)
- **Memory:** 28 GiB total (24 GiB model serving + 3 GiB agent/browser + 1 GiB shared memory)

### Software Requirements

- Red Hat OpenShift Container Platform 4.14+
- Red Hat OpenShift AI 2.19+
- NVIDIA GPU Operator installed and configured
- `oc` CLI tool ([install guide](https://docs.redhat.com/en/documentation/openshift_container_platform/4.17/html/cli_tools/openshift-cli-oc))

## Deploy

### Step 1: Clone the repo

```bash
git clone https://github.com/rh-ai-quickstart/ai-browser-testing.git
cd ai-browser-testing
```

### Step 2: Install

```bash
make install
```

This single command creates the project, installs the Helm chart, waits for the model, and prints the dashboard URL when ready. The model download takes several minutes on first install.

> **Note:** The vLLM image reference in `chart/values.yaml` may need updating to match your Red Hat OpenShift AI version. Check your cluster's existing image with:
> ```bash
> oc get servingruntime -n redhat-ods-applications -o jsonpath='{.items[0].spec.containers[0].image}'
> ```
> Then update `model.vllmImage` in `chart/values.yaml` and run `make upgrade`.

### Step 3: Open the dashboard

When `make install` finishes, it prints the dashboard URL. You can retrieve it anytime:

```bash
make dashboard
```

The dashboard shows:
- **Status** — current run number, iteration count, live updates
- **Watch the AI** — link to the noVNC live browser view where you can see Chrome being operated by the AI in real-time
- **TODO App** — link to the application under test
- **Test results** — step-by-step PASS/FAIL for the current run
- **Test history** — scores from all completed runs

The agent runs tests continuously in a loop. Each run takes about 2 minutes.

### Configuration

Edit `chart/values.yaml` to customize:

```yaml
model:
  name: qwen3-8b                              # InferenceService name
  storageUri: "hf://RedHatAI/Qwen3-8B-FP8-dynamic"  # Model source
  vllmImage: "quay.io/modh/vllm:rhoai-2.19-cuda"    # vLLM runtime image

agent:
  image:
    repository: quay.io/rh-ai-quickstart/ai-browser-testing
    tag: latest
```

Apply changes with `make upgrade`.

### Delete

Remove all quickstart resources:

```bash
make uninstall
```

## How It Works

The agent follows a simple loop:

1. **Send test instructions** to the Qwen3 8B model along with available browser tools (navigate, snapshot, click, type, wait)
2. **The model responds** with tool calls — deciding what browser action to take next
3. **Playwright MCP executes** the tool call against Chrome and returns an accessibility snapshot of the page
4. **The model reads the snapshot**, verifies the result, and decides the next action
5. **Repeat** until all test steps are complete

The test prompt instructs the agent to navigate to the TODO app, add items, toggle completion, delete items, and verify each action. The model reports PASS/FAIL for each step. Results are displayed on the dashboard in real-time.

Because the agent uses natural-language instructions rather than coded selectors, the same approach works across different applications — change the prompt, not the code.

## Reference

- [Playwright MCP](https://github.com/microsoft/playwright-mcp) — Model Context Protocol server for browser automation
- [Model Context Protocol](https://modelcontextprotocol.io/) — Open standard for connecting AI models to tools
- [Qwen3 8B FP8 on Hugging Face](https://huggingface.co/RedHatAI/Qwen3-8B-FP8-dynamic) — The model used in this quickstart
- [vLLM Tool Calling](https://docs.vllm.ai/en/latest/features/tool_calling.html) — How vLLM supports function calling
- [Red Hat OpenShift AI](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/) — Platform documentation

## Tags

- **Title:** Automate Browser Testing with AI on OpenShift AI
- **Description:** Deploy an AI agent that autonomously tests web applications by operating a real browser, driven by natural-language instructions and an open-source LLM.
- **Industry:** Media and IT services
- **Product:** Red Hat OpenShift AI
- **Use case:** QA automation, testing
- **Partner:** N/A
- **Contributor org:** Red Hat
