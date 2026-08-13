import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SkillsToolsEntry } from "./skills-tools-entry";

describe("SkillsToolsEntry", () => {
  it("明确 Skills 与 Tools 的第一阶段支持范围", () => {
    render(<SkillsToolsEntry chapter="陆" />);

    expect(screen.getByRole("heading", { name: "Skills/Tools" })).toBeInTheDocument();
    expect(screen.getByText(/声明式 Skill/)).toBeInTheDocument();
    expect(screen.getByText(/现有 Tool Catalog/)).toBeInTheDocument();
    expect(screen.getByText(/受限 HTTP Tool/)).toBeInTheDocument();
    expect(screen.getByText(/不支持任意代码\/Shell/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "进入 Skills/Tools 管理" })).toHaveAttribute(
      "href",
      "/settings/extensions",
    );
  });
});
