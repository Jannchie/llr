# 助手 UX：从 preferred-harness 借什么，以及如何让它迭代

> **状态**：实施计划第 1–4 步已落地（`compare`/`measure`/diff/prompt、工具行展开与状态行、API 兜底与 steer、费用/thinking/composer）。第 5–6 步未做。
> 与本文的偏差：兜底只在 `stopReason === "stop"` 的回合触发（出错/中断的回合若留下 followUp，会在下一次 prompt 开头被消费）；`/agent/steer` 在会话空闲时答 409（空闲时的 steer 会被塞进下一次 prompt）；每回合用量作为累计值挂在助手回复上而不是 user entry；`measure` 的像素读回走渲染器新拆出的 `readFrame()`（`toBlob()` 复用它）；步骤 3 里 `agent_end` 附 `messages.length` 推迟到第 5 步（retract）再加。

面向 LLR 维护者的设计文档。范围：`apps/api/src/agent.ts`、`apps/web/src/composables/useAssistant.ts`、`App.vue` 的 Assistant 块（L1884–2148）与 chat 模板（L2493–2533）。不改源码。

## 结论：按 价值 ÷ 成本 排序的建造清单

1. **迭代循环靠三件事一起落地**：新 system prompt（含强度标定与"必须 compare 后才能结束"）、`compare` 工具（before/after 拼图）、`measure` 工具（直方图统计数字）。三者都只改 App.vue 的 Assistant 块，半天。
2. **API 侧兜底**：在 `turn_end` 监听里，若本回合无 tool call 且 `set_edit` 之后没有 `compare`/`view_image`，用 `agent.followUp()` 注入一句提醒，每次 prompt 最多一次。pi-agent-core 原语现成，20 行。
3. **`set_edit` 返回 diff 而非全量 `describeEdit()`**：每回合省 ~800 token，也让工具行可以直接显示"exposure 0 → +0.4"。
4. **工具行可展开**：原生 `<details>`，summary 保留现在的一行，展开显示 set_edit 的 diff、view_image/compare 实际送出的缩略图、measure 的数字。用户第一次能看见"模型看到了什么"。
5. **每回合 token/费用**：`message_end` 已把 `usage` 原样送到浏览器（agent.ts:96–98），只差读出来显示。
6. **中断留痕**：现在 abort 后空回复被删（useAssistant.ts:137），一行"已停止"状态行即可。
7. **运行中发送 = steer**：新增 `/agent/steer`，繁忙时 composer 不再 409，而是把话插进下一次 LLM 调用。不做 queue island。
8. **thinkingLevel**：`ModelSpec` 加可选 `thinking`，API `clampThinkingLevel` 后写入 `agent.state.thinkingLevel`；composer 里模型旁一个小下拉。
9. **retry / edit-and-resend / delete**：API 加 `/agent/retract`（`agent.state.messages = messages.slice(0, n)`），浏览器同时把照片 undo 到该回合的 baseline 快照——这是照片编辑器区别于代码 agent 的一点：撤回模型的记忆必须连带撤回它的编辑。
10. **composer 细节**：IME 组合中的 Enter 不发送；空状态建议 chips 填入而非发送；答案用极简 markdown。
11. **转录持久化**：只持久化展示用 entries（跟随现有 per-photo 持久化），模型上下文不恢复。低优先。

**不移植**：workspace / cwd / 分支切换、shell 与文件 mod、safe/yolo 审批（LLR 的每个 tool 都可 undo，审批是负价值）、MCP、skills、subagent、schedule、goal/todo、JSONL 协议日志与 projection 层、虚拟列表（一张照片的对话不会长到需要）、bill 报表弹窗、trace/events 视图。

---

## A. 从 preferred-harness 借的机制

每项：PH 做法 → pi-agent-core 是否已有原语 → LLR 最小落地 → 权衡。

### A1. 工具行：一行摘要 + 点击展开参数与结果

**Why**：用户现在只看到"调整 · sliders, hsl"（App.vue:2146–2148 `toolSummary` 只列 key），不知道模型改了什么、看到了什么图。信任来自可见。

**How**：PH 的 `ToolRow.vue:161–179` 是一行 `ActivityRow`（icon / label / detail=argument / meta=result preview），点击在下方内联展开 `ToolDetails.vue:71–137`（参数 → 输出 → payload，三段，有 CopyButton）；`ToolDetails.vue:12–28` 解释了为什么从侧栏改为内联：细节应贴着产生它的那一行。四种状态 running/done/failed/abandoned 用 icon+颜色区分（`ToolRow.vue:83–111`）。一个回合的多行可折成一行 `StepsClusterRow.vue:9–25`（计数、时长、费用、失败数不能被折叠掉）。

LLR 落地：`ChatEntry.tool` 加 `result?: ToolContent[]`，`runTool` 把 `tool.run()` 的返回同时存进 entry（useAssistant.ts:103）。模板把 `.chat-tool` 换成 `<details class="chat-tool"><summary>…</summary>…</details>`：
- `set_edit`：显示 diff（见 B7.1），不是 args 的 key 列表；
- `view_image` / `compare`：`<img :src="data:image/jpeg;base64,…">` 缩略图，就是送给模型的那张；
- `measure`：数字表。
不做 StepsCluster：一个照片回合通常 3–6 个 tool，折叠反而藏掉信息。

**Trade-off**：entries 持有 base64 图，内存随回合增长（1024px JPEG ~150 KB/张）；对话清空即释放，可接受。

### A2. Thinking 流式显示

**Why**：开启 thinkingLevel 后，模型会先思考 5–20 s，没有反馈用户会以为卡死。

**How**：PH `ThinkingBlock.vue:73–92` 与工具行同形（ActivityRow，label "思考中"，detail 跟随最后一行，meta 显示用时/token），点击展开完整 reasoning。pi-ai 流事件里已有 `thinking_start` / `thinking_delta`（pi-ai `types.d.ts:205–209`），LLR 的 `serialize` 已透传 `message_update`（agent.ts:92–95），浏览器只处理 `text_delta`（useAssistant.ts:133）。落地：`ChatEntry.assistant` 加 `thinking: string`，`thinking_delta` 追加；模板一行 `<details>`，summary 为 "思考 · {n} 字"。

**Trade-off**：没有 PH 的 duration/estimated-tokens 两级精度，够用。

### A3. Markdown

**Why**：模型答复会带 `**加粗**` 和列表；LLR 现在原样输出星号（App.vue:2512）。

**How**：PH 用 `@fadedown/vue` 的流式增量渲染（`lib/markdown.ts:43–60`，避免半截代码围栏闪烁），`md.set({ html: false })` 是唯一 XSS 防线（`lib/markdown.ts:20–22`）。LLR 的答复被 prompt 约束为"简短、不列每个值"，段落 + 加粗 + 列表足够。落地：引入 `@fadedown/vue`（维护者自己的库，已知行为），或 20 行手写 paragraph/bold/list 渲染成 VNode。二者都必须转义 HTML。不引 shiki——照片助手不会输出代码块。

**Trade-off**：手写版遇到表格/链接退化为原文；fadedown 多一个依赖但零维护。

### A4. 消息动作：retry / edit-and-resend / delete，带模型视角撤回

**Why**：换个说法重来是最常见的操作；现在只能 Clear 全部。

**How**：PH 把三者统一为一个 `turns_retracted {start, end, reason: "retried"|"edited"|"deleted"}` 事件（`packages/agent/src/protocol.ts:718–724`），`retractFrom` 在有回合运行时拒绝（`runtime.ts:1090–1124`），action `{type:"retract", turnId, reason, resend?}` 把撤回与随后的重发当作一个动作（`actions.ts:68, 271–292`）；`MessageActions.vue:53–93` 是 hover 时出现的 copy/edit/retry/delete 四个按钮加费用文字。pi-agent-core 侧：`agent.state.messages` 是可赋值的 accessor（pi-agent-core `types.d.ts:264–266`），所以撤回 = `agent.state.messages = agent.state.messages.slice(0, n)`。

LLR 落地：
- API `POST /agent/retract {session, keep}`；`agent_end` 序列化时附上 `messages.length`（agent.ts:99–100），浏览器把它记在该回合的 user entry 上作为下一回合的 `keep`。
- 浏览器每个回合记 `baseline: Snapshot`（`captureSnapshot()` App.vue:732），retry/edit 时先 `applySnapshot(baseline)` + 提交一步历史（"撤回助手编辑"），再 retract + 重发。delete 同理但不重发。
- 只允许对最后一个回合操作（PH 也是 `canEdit`/`canRetry` 按位置给），避免中间撤回后历史与照片状态错位。

**Trade-off**：用户在回合之后手动动过滑块，回滚 baseline 会连带撤掉；提示一句并放进历史即可（可 redo）。

### A5. 运行中发送：steer 而非 queue

**Why**：模型跑 4 轮循环要 20–40 s，用户中途想说"别那么暖"。现在 API 直接 409（index.ts:194）。

**How**：PH 里 `prompt` 在运行中就是排队（`actions.ts:202` → `agent.enqueue()`，`runtime.ts:894–921`），`steer` 需要有活动回合（`actions.ts:214–219`），在每个 step 边界由 `consumePendingSteering()` 提交为 `form: "steering"` 的 user 消息（`turn-runner.ts:359–368, 989–1017`）。浏览器默认 queue、island 里的按钮可改 steer（`Composer.vue:126–129`，`QueuedMessage.vue:34–45`，`use-session.ts:376–426`）。pi-agent-core 原生有两者：`agent.steer()` 在当前回合 tool 执行完、下一次 LLM 调用前注入（`agent-loop.js:133`, `types.d.ts:166–176`），`agent.followUp()` 在本应停止时注入（`agent-loop.js:136–141`）。注入的消息会以 `message_start`/`message_end` 出现在同一条 SSE 流里（`agent-loop.js:94–97`），浏览器无需新通道。

LLR 落地：`/agent/steer` → `agent.steer({ role: "user", content: [{ type: "text", text }], timestamp: Date.now() })`；`useAssistant.send` 在 `busy` 时改调 steer 并把 user entry 推入（`message_start` 到达时去重）。不做 queue island：照片回合短，"排队等它做完再说"没有场景。

**Trade-off**：steer 之后模型可能仍先把当前那步做完再改向，属预期。

### A6. 中断与错误呈现

**Why**：Stop 之后没有任何痕迹（空 assistant entry 被 useAssistant.ts:137 删掉，`stopReason === "aborted"` 不走 138 的 error 分支），用户不知道是不是停成了。

**How**：PH `TurnEndingRow.vue:29–50, 75–93`：失败红、中断琥珀，中断只有一行标题不带正文；错误正文用 `<pre>` 可选中可滚动（L109–112），并叠加可识别错误的处理提示（L52–68，分类在 `turn-failure.ts:29–100`：contextOverflow / credentials / rateLimit / unreachable）。中断本身不给模型任何消息，半截 step 直接丢弃（`turn-runner.ts:328–331`）——LLR 沿用 pi-agent-core 的 aborted 消息即可，不必额外告诉模型。LLR 落地：`ChatEntry` 加 `{ kind: "status"; status: "interrupted" | "failed"; text? }`；`message_end` 遇 `aborted` 推 interrupted，`error` 推 failed（把现有 `reply.error` 迁过去）。`.chat-error` 已有样式（style.css:1923），加一个 amber 变体。

### A7. 每回合 token / 费用

**Why**：图像回合的成本不直观，B 部分要把回合数放开，必须让用户看得见。

**How**：PH 由 `ProtocolLedger` 从 `step_completed.usage` / `turn_completed.usage` 逐事件折叠出 per-turn 与 per-model 花费（`ledger.ts:24–61`，`protocol.ts:438–454, 563–582`），`MessageActions.vue:95–97` 在动作按钮旁直接写费用文字而非 tooltip；`StepsClusterRow` 的折叠行也带 token。pi-ai 的 `AssistantMessage.usage` 含 `input/output/cacheRead/totalTokens` 与 `cost.total`（pi-ai `types.d.ts:124–143`），provider 在流结束时调 `calculateCost`（`providers/anthropic.js:343`），LLR 的 `serialize` 对 assistant 的 `message_end` 原样透传（agent.ts:96–98）。落地：`message_end` 时累加到本回合 user entry 的 `usage`，在 assistant 气泡下方灰字 `1.2k in · 340 out · $0.012`。

**Trade-off**：`resolveModel` 用兄弟模型模板的价格（agent.ts:51–53）时费用是估的；`Model.id !== known` 时加 `~` 前缀。

### A8. composer：模型 + thinking 选择器；键盘

**Why**：设置离输入框太远，PH 的理由原文："决定用哪个模型、想多久，是写 prompt 时做的决定"（`ComposerControls.vue:9–18`）。

**How**：PH 模型下拉按 provider 分组、无 key 的沉底并标注（`ComposerControls.vue:31–82`），effort 下拉只在该模型声明了 `reasoningEfforts` 时出现（L84–95），空串 = provider 默认。键盘：Enter 发送、Shift+Enter 换行，且用 `ime.composing(event)` 而不是单看 `isComposing`——WebKit 提交词的那个 Enter 到达时 `isComposing` 已是 false（`Composer.vue:285–289, 321–325`）；↑/↓ 在空框时翻输入历史（L335–346）。

LLR 落地：`SelectMenu` 从 `chat-head`（App.vue:2495）移到 `chat-compose` 内左下；旁边加 thinking 下拉（off/low/medium/high），`ModelSpec` 加 `thinking?: ThinkingLevel`，`PromptBody` 透传，API `agent.state.thinkingLevel = clampThinkingLevel(model, level)`（pi-ai `models.d.ts:11`；非 reasoning 模型 `getSupportedThinkingLevels` 只返回 `["off"]`，`models.js:31–33`）。Enter 处理换成 `@keydown.enter="e => !e.shiftKey && !e.isComposing && e.keyCode !== 229 && submit()"`——维护者本人通过 Windows 浏览器用中文输入法（见 memory），现有 `.enter.exact`（App.vue:2525）在 IME 提交时会误发。不做输入历史。

### A9. 空状态建议 chips

**How**：PH `SuggestionIsland.vue:5–11`：chips 填入 composer 而非直接发送，"发送键仍是话变成用户自己的话的地方"。LLR 落地：`chat.empty`（i18n.ts:74）下方 4 个静态 chips（"自动调整曝光和白平衡" / "电影感、低饱和" / "拉直地平线并按 3:2 裁切" / "突出主体、压暗四周"），点击写入 `chatDraft`。20 行。

### A10. 转录持久化

**How**：PH 的 JSONL 日志是 source of truth（`journal.ts:94–127`），`syncModelHistoryFromLog()` 是模型上下文的唯一写者（`runtime.ts:1356–1358`），浏览器刷新时拿快照再从 `snapshot.seq` 续流（`use-session.ts:289–320`）。LLR 的对话是页面级内存（useAssistant.ts:59–61，注释明说刷新即失），API 会话 60 分钟过期（agent.ts:33）。最小落地：把 `entries`（去掉 base64 图）塞进现有 per-photo 持久化（`captureEdit` App.vue:875 旁），恢复后只展示，模型上下文从零开始；system prompt 附一句"此前对话摘要：…"（最近 3 条 user/assistant 文本）。不复刻日志协议。

**Trade-off**：恢复后 retry/steer 不可用（无 API 会话），按钮隐藏即可。

### A11. 用户手动改了滑块，让模型知道

**Why**：一张照片一个会话，用户在两次提问之间会自己动滑块；模型下一回合仍以为是它上次留下的值，`set_edit` 的绝对值会把用户的改动覆盖掉。

**How**：PH 的 `agent.injectContext({ producer, content, state, summary })` 在下一个 step 边界投递、同 producer 只保留最新、`state` 未变则丢弃（`runtime.ts:997–1046`，`turn-runner.ts:307, 969–984`），显示为 `form: "notice"` 的折叠行。LLR 不需要队列：`send()` 时对比 `lastAgentEndSnapshot` 与当前 `captureSnapshot()`，有差异就在 user 文本前拼一段 `[The user changed by hand since your last turn: exposure +0.2 → +0.5, …]`，UI 上按 status 行显示而非用户气泡。pi-agent-core 侧无需改动。

---

## B. 让助手迭代而不是一发即停

### B1. 诊断：为什么一轮就停

三个原因，缺一不可：(1) prompt 只说"refine if needed"（App.vue:2121），模型自认为 needed 的门槛极高；(2) `view_image` 只给"之后"的图，没有"之前"作对照，模型无法判断改动是否过头，只能说"看起来不错"；(3) 没有任何数字——模型不知道 +30 highlights 是"轻微"还是"很强"，也没法核对"高光是否还有剪切"。三者各对应 B2/B3/B4，B5 是硬兜底。

### B2. Prompt：强制循环 + 强度标定

替换 `assistantSystemPrompt()`（App.vue:2118–2126）第 2 段为：

```
Workflow, in this order, every time you change the photo:
1. Look first: get_edit for the current values and view_image for the picture.
2. Apply with set_edit (absolute values, not deltas). Change 2–4 controls per round, not everything at once.
3. Judge with compare (before/after side by side) and measure (numbers). Ask: is it too much, too little, did anything clip?
4. Refine with another set_edit, then compare again. Expect 2–4 rounds; stop when compare shows the intent
   without over-correction. Never finish a turn with a set_edit you have not looked at.
Strength calibration (Lightroom semantics): exposure ±0.3 EV is one visible step, ±1 EV is dramatic.
On -100..100 sliders, ±10 is subtle (visible only in compare), ±30 clearly visible, ±60 strong, beyond that
is a special effect. Temperature: ±300 K subtle, ±1000 K obviously warm/cool. Start at the subtle-to-visible
end and increase only if compare shows too little.
```

Prompt 层级要一致：删掉现在的"Keep adjustments natural and proportionate"（被标定段覆盖）。`view_image` 的描述改为"Prefer compare after set_edit; view_image is for the first look"。

### B3. `compare` 工具：before/after 拼图

**Why**：hold-to-compare 是人类判断强度的方式（App.vue:1502 `startCompare`），模型也需要同一件事。

**How**：`buildPipelineParams(snapshot, { preview: false })` 已接受任意 `Snapshot` 渲染（App.vue:1212，为导出而设），curve LUT 从快照重烘（`buildToneCurveLUT(s.curve, currentBasic(s.recipe))`，App.vue:637–639, 653）。所以 before 不需要动 reactive 状态，也不进历史：

```ts
async function captureSnapshotJpeg(s: Snapshot | null, maxEdge: number): Promise<Blob> {
  const params = s ? buildPipelineParams(s, { preview: false }) : buildPipelineParams(undefined, { preview: false });
  webglRenderer.uploadCurveLUT(buildToneCurveLUT(s?.curve ?? toneCurve.value, currentBasic(s?.recipe ?? recipe)));
  webglRenderer.setViewWindow(null); webglRenderer.setPreviewScale(maxEdge / Math.max(imageW.value, imageH.value));
  webglRenderer.draw(params);
  const blob = await webglRenderer.toBlob("image/jpeg", 0.85);
  bakedBasic = null; scheduleWebGLDraw();   // next frame rebakes the live LUT
  return blob;
}
// compare: two 512px halves drawn onto one 2D canvas, thin gap, "before | after" labels burnt in.
```

baseline 的选择：`turnBaseline = captureSnapshot()` 在 `send()` 开始时取一次（跟 A4 复用同一个快照）；`compare` 参数 `against: "turn_start" | "previous_edit"`，默认 `turn_start`，后者用上一次 `set_edit` 前的快照——用来判断"这一步加得对不对"。裁切统一用当前 crop（比较的是影调不是构图），`showOriginal` 路径的 `baselineParams()`（App.vue:1416）不用：它是"零编辑"，不是"本回合之前"。

**Trade-off**：一次 compare 两次 draw + 一次 2D 合成，~100 ms；期间 `bakedBasic` 置空让下一帧重烘，与 `capturePreview` 现有做法一致（App.vue:1928–1933）。

### B4. `measure` 工具：给模型数字

**Why**：模型判断"是否剪切"、"是否偏暗"靠看图并不可靠，256 桶直方图就在手边。

**How**：`webglRenderer.readHistogram()` 返回 r/g/b/l 各 256 桶（pipeline-renderer.ts:1832，输出即显示编码 sRGB，与直方图面板同源）。`clipIndicators` 的阈值逻辑已存在（histogram.ts:74–86）。工具返回：

```json
{ "luminance": { "p1": 4, "p5": 11, "p50": 96, "p95": 221, "p99": 248, "mean": 104 },
  "clipped": { "shadows_pct": 0.3, "highlights_pct": 1.8, "highlight_channels": "R+G" },
  "regions": { "top": 168, "middle": 101, "bottom": 52 }, "chroma_mean": 0.21,
  "hint": "highlights clip in R+G: lower highlights/whites or exposure" }
```

percentile 与 clipped 直接从 `bins.l` 累加；`regions`（上/中/下三段亮度均值）与 `chroma_mean` 需要像素：复用 `capturePreview` 的一次小尺寸 draw（256 px 长边）`readPixels` 后在 JS 里算，~5 ms。`hint` 只在阈值触发时给，是 `clipIndicators` 的文字版。

**Trade-off**：数字是显示编码的，不是场景线性——对模型足够，与它在 Lightroom 直方图上学到的语义一致。

### B5. API 侧兜底：turn_end + followUp

**Why**：prompt 是软约束；总有模型改完就宣布成功。

**How**：pi-agent-core 的 `Agent` 不暴露 `shouldStopAfterTurn`（只在 `AgentLoopConfig`，`createLoopConfig` 私有，agent.js:276–301），`afterToolCall.terminate` 只能提前停、不能续（types.d.ts:52–56）。但 `turn_end` 监听是被 await 的（agent.d.ts:54–63），且 loop 在 `turn_end` 之后才轮询 follow-up（agent-loop.js:123, 136），所以：

```ts
// per session: editedSinceLook = false; nudged = false  (reset on prompt start)
// in the subscribe callback:
if (e.type === "tool_execution_start") editedSinceLook = e.toolName === "set_edit" ? true
  : ["view_image", "compare"].includes(e.toolName) ? false : editedSinceLook;
if (e.type === "turn_end" && !hasToolCalls(e.message) && editedSinceLook && !nudged) {
  nudged = true;
  s.agent.followUp({ role: "user", content: [{ type: "text",
    text: "You changed the edit but did not look at the result. Call compare, judge it, refine if needed, then answer." }],
    timestamp: Date.now() });
}
```

浏览器在 `message_start` 收到这条 user 消息时按 `status` 行显示"要求助手检查结果"，而不是当作用户说的话（对应 PH 的 `form: "notice"`，AGENTS.md:66）。每次 prompt 只提醒一次，避免死循环。

**Trade-off**：API 因此知道了 `set_edit`/`compare` 两个工具名，破坏了"API 对摄影一无所知"（ARCHITECTURE.md:88–89）。折中：`ToolSpec` 加 `role?: "mutate" | "inspect"`，浏览器标注，API 只认 role。

### B6. thinkingLevel

按 A8 落地。默认 `off`；对 reasoning 模型建议 `low`——循环里每轮都要想一次，`high` 会把 4 轮拉到分钟级。作为 `ModelSpec` 的一部分持久化在 localStorage（useAssistant.ts:34–43 已有）。

### B7. Token 与费用

图像 token 便宜：Anthropic ≈ w·h/750，1024×683 ≈ 930；OpenAI 512 px tile 计费，1024×683 ≈ 4 tile ≈ 765。真正的开销是**上下文复读**：每张图与每次 `describeEdit()`（~40 个 slider 带 min/max/default ≈ 800 token，加 masks/curve）都留在上下文里，后续每一次 LLM 调用都重读。4 轮 ≈ 4×(图 900 + edit 800 + 文本 300) ≈ 8k 累计，第 5 次调用读 8k。控制手段，按省得多到少：
1. `set_edit` 只返回改动的字段（diff），`get_edit` 保留全量 —— 每轮省 ~700。
2. `compare` 两半各 512 px（合计 ≈ 1024×512 ≈ 700 token），`view_image` 默认 768（≈ 520）。
3. `measure` 纯文本 ~150 token，鼓励模型用它代替再看一次图。
4. Anthropic/OpenAI 的 prompt cache 让复读走 cacheRead（约 1/10 价）；pi-ai 已透传 `cacheRead`，A7 显示出来即可。
一次 4 轮、Claude 级模型的回合约 $0.03–0.06；显示出来比再压更重要。

### B8. 推荐组合

B2 + B3 + B4 + B7.1/7.2 一起上（同一个文件，互相依赖：prompt 提到 compare/measure，它们必须存在）；B5 第二步；B6 随 A8。不建议：只改 prompt（没有 before 图模型仍然只能说"看起来不错"）；`afterToolCall` 篡改 `set_edit` 结果附带图片（把 mutate 和 inspect 混在一起，模型学不到"看"是独立的一步）。

---

## 实施计划（每步 ≤ 1 天，可独立提交）

1. **迭代核心**（App.vue）：`compare` + `measure` 工具、`set_edit` 返 diff、`view_image` 默认 768、新 prompt 文本、turnBaseline 快照。手测：一句"暖一点、亮一点"应看到 2–4 轮 set_edit→compare。
2. **工具行展开**（useAssistant.ts + App.vue 模板 + style.css）：entry 存 result，`<details>`，缩略图与 diff 渲染；status 行（中断/失败/nudge）。
3. **API 兜底 + steer**（agent.ts, index.ts, protocol.ts, useAssistant.ts）：`ToolSpec.role`、turn_end followUp、`/agent/steer`、`agent_end` 附 `messages.length`。
4. **费用与 thinking**：`usage` 读出显示；`ModelSpec.thinking`、`PromptBody` 透传、`clampThinkingLevel`；composer 重排（模型/thinking 下拉移到输入框下方）、IME Enter 修复、chips。
5. **retry / edit / delete**：`/agent/retract`、baseline 回滚进历史、仅末回合可用。
6. **markdown 与持久化**：`@fadedown/vue` 或手写渲染；entries 进 per-photo 持久化（去图）。

每步之后跑 `.claude/skills/verify` 的驱动流程确认一次对话；第 1 步另加 vitest 覆盖 `measure` 的百分位/剪切计算（纯函数，放 `rendering/histogram.ts` 旁）。
