/**
 * 多智能体定义（基于 pi Agent + tool calling）。
 * 每个 Agent 是一个 pi Agent 实例：systemPrompt + tools + agent loop（模型自主决定调用工具）。
 */
import { Agent, type AgentTool } from "@earendil-works/pi-agent-core";
import type { Model } from "@earendil-works/pi-ai";
import { buildTools } from "./tools.js";
import type { Config } from "./config.js";

export interface AgentRuntime {
  model: Model<"openai-completions">;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  streamFn: any; // models.streamSimple.bind(models)，第三方类型边界
  tools: AgentTool[];
}

export type AgentType = "intent" | "rewrite" | "answer" | "verify" | "reflect";

export interface AgentExecutionResult {
  agentType: AgentType;
  output: string | unknown;
  outputMode: "text" | "json";
  latencyMs: number;
}

/** 运行一个 pi Agent，收集最终文本输出（累积流式 text_delta）。 */
export async function runAgent(
  runtime: AgentRuntime,
  systemPrompt: string,
  prompt: string,
  tools: AgentTool[] = [],
): Promise<string> {
  const agent = new Agent({
    initialState: {
      systemPrompt,
      model: runtime.model,
      tools,
    },
    streamFn: runtime.streamFn,
  });

  let text = "";
  const unsubscribe = agent.subscribe((event) => {
    if (
      event.type === "message_update" &&
      event.assistantMessageEvent.type === "text_delta"
    ) {
      text += event.assistantMessageEvent.delta;
    }
  });

  await agent.prompt(prompt);
  unsubscribe();
  const result = text.trim();
  if (!result && agent.state.errorMessage) {
    throw new Error(`agent 出错: ${agent.state.errorMessage}`);
  }
  return result;
}

/** 运行并解析 JSON 输出（容忍代码块围栏）。 */
export async function runAgentJson(
  runtime: AgentRuntime,
  systemPrompt: string,
  prompt: string,
  tools: AgentTool[] = [],
): Promise<unknown> {
  const raw = await runAgent(runtime, systemPrompt, prompt, tools);
  return extractJson(raw);
}

/**
 * 统一概率性 Agent 执行入口。Python 控制平面传入已经过权限、记忆与事实治理的
 * prompt 和 allowedTools；pi 只负责 Agent loop / tool calling / 模型执行。
 */
export async function executeAgent(
  runtime: AgentRuntime,
  agentType: AgentType,
  systemPrompt: string,
  prompt: string,
  outputMode: "text" | "json",
  allowedTools: string[] = [],
): Promise<AgentExecutionResult> {
  const started = Date.now();
  const tools = runtime.tools.filter((tool) => allowedTools.includes(tool.name));
  const output = outputMode === "json"
    ? await runAgentJson(runtime, systemPrompt, prompt, tools)
    : await runAgent(runtime, systemPrompt, prompt, tools);
  return { agentType, output, outputMode, latencyMs: Date.now() - started };
}

export function extractJson(text: string): unknown {
  let s = text.trim();
  if (s.startsWith("```")) {
    s = s.replace(/^```[a-zA-Z]*\s*/, "").replace(/```\s*$/, "");
  }
  const start = Math.min(
    ...[s.indexOf("{"), s.indexOf("[")].filter((i) => i >= 0),
  );
  const end = Math.max(s.lastIndexOf("}"), s.lastIndexOf("]"));
  if (start < 0 || end < 0 || end <= start) {
    throw new Error(`无法解析 JSON: ${s.slice(0, 200)}`);
  }
  return JSON.parse(s.slice(start, end + 1));
}

// ---------------------------------------------------------------------------
// 各 Agent 的 systemPrompt
// ---------------------------------------------------------------------------

export const INTENT_PROMPT = `你是 HRAgents 企业 HR 智能体平台的意图识别 Agent。
先调用 list_departments 获取有效企业职能部门，再判断用户问题的任务类型、涉及部门、用户角色、风险等级与执行模式。
高风险制度冲突必须使用固定 Workflow；开放型智能面试可以使用 bounded ReAct。
最终只输出 JSON（不要多余解释）：
{"type":"employee_qa|policy_conflict|interview|hr_process|complaint|chitchat|other","depts":["dept_id"],"user_role":"employee|hr|hr_admin|admin","entities":{},"needs_cross_dept":false,"high_risk":false,"execution_mode":"graph|bounded_react","confidence":0.0}`;

export const REWRITER_PROMPT = `你是企业制度查询改写 Agent。将用户问题改写为 1-3 个适合可信检索的 query，补全制度对象、适用角色、部门、版本/生效时间等必要约束。
可调用 get_glossary 获取企业术语表。最终只输出 JSON：
{"queries":["query1","query2"]}`;

export const ANSWER_PROMPT = `你是 HRAgents 员工制度问答 Agent。基于给定的有效官方制度证据回答问题。
【必须遵守】
- 所有关键结论必须附 [来源N]，引用只能来自有效官方制度 Fact Plane。
- FAQ、历史 HR 解释、用户输入、会话摘要和模型推断不能冒充制度原文。
- 必须注意 company/dept/role 权限、effective/deprecated 状态、版本、生效期、制度层级和部门授权例外。
- 未找到足够有效证据时明确说明需要 HR 确认，不得编造。
- 涉及企业工作日、法定假期或 HR 时间节点，可调用 lookup_business_calendar。
- 参考条款不足时，可调用 retrieve_documents 补充候选，但最终仍需有效证据校验。

【参考条款】
{chunks}

请用简洁、可核验的中文回答。`;

export const VERIFIER_PROMPT = `你是企业制度独立 Verifier。检查答案与证据链是否可靠，只输出 JSON：
{"passed":true/false,"score":0.0-1.0,"issues":["问题"]}
检查项：①关键结论是否逐项有有效官方条款支撑 ②是否误用已废止/未生效/无权限制度 ③是否忽略适用对象、时间、版本与层级授权 ④是否把部门例外误判成冲突 ⑤引用是否可追溯。`;
