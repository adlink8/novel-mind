import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsSection } from "./settings-section";

describe("SettingsSection", () => {
  it("渲染章节字、标题与 children", () => {
    render(
      <SettingsSection chapter="壹" title="账户">
        <p>内容</p>
      </SettingsSection>
    );
    expect(screen.getByText("壹")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "账户" })).toBeInTheDocument();
    expect(screen.getByText("内容")).toBeInTheDocument();
  });

  it("渲染 action 区域", () => {
    render(
      <SettingsSection chapter="贰" title="策略" action={<button>操作</button>}>
        <p>正文</p>
      </SettingsSection>
    );
    expect(screen.getByRole("button", { name: "操作" })).toBeInTheDocument();
  });
});
