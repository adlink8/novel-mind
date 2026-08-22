import { describe, expect, it } from "vitest";

import {
  hasAgentIntent,
  resolveSendRouting,
  type BackendRoutingHint,
} from "./agent-routing";

describe("hasAgentIntent", () => {
  it("flags illustration intent", () => {
    expect(hasAgentIntent("为第一章画一张插图")).toBe(true);
    expect(hasAgentIntent("给这段配图")).toBe(true);
    expect(hasAgentIntent("生成一幅主角的画像")).toBe(true);
    expect(hasAgentIntent("画一下女主")).toBe(true);
    expect(hasAgentIntent("illustrate this scene")).toBe(true);
  });

  it("flags continuation intent", () => {
    expect(hasAgentIntent("请续写这段故事")).toBe(true);
    expect(hasAgentIntent("接着上一章继续写")).toBe(true);
    expect(hasAgentIntent("接下来会发生什么")).toBe(true);
  });

  it("does not flag ordinary questions", () => {
    expect(hasAgentIntent("主角的动机是什么？")).toBe(false);
    expect(hasAgentIntent("这段话在说什么")).toBe(false);
    expect(hasAgentIntent("")).toBe(false);
  });
});

describe("resolveSendRouting", () => {
  it("defaults to reader_chat for ordinary questions", () => {
    expect(resolveSendRouting("主角的动机是什么？")).toEqual({
      mode: "reader_chat",
    });
  });

  it("routes agent intent to agent without a skill (backend auto-routes)", () => {
    expect(resolveSendRouting("请为这段配图")).toEqual({ mode: "agent" });
    expect(resolveSendRouting("请续写")).toEqual({ mode: "agent" });
  });

  it("honors a backend suggested skill without user-facing selection", () => {
    const hint: BackendRoutingHint = {
      suggestedSkill: "answer-reading-question",
    };
    expect(resolveSendRouting("随便问问", hint)).toEqual({
      mode: "agent",
      skill: "answer-reading-question",
    });
  });

  it("prefers the backend suggestAgent hint over the client heuristic", () => {
    const hint: BackendRoutingHint = { suggestAgent: true };
    // 即使消息是普通问句，后端说走 agent 就听后端的
    expect(resolveSendRouting("普通问题", hint)).toEqual({ mode: "agent" });
  });

  it("ignores null/undefined hints", () => {
    expect(resolveSendRouting("普通问题", null)).toEqual({
      mode: "reader_chat",
    });
    expect(resolveSendRouting("普通问题", undefined)).toEqual({
      mode: "reader_chat",
    });
  });
});
