import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExtensionsPage } from "./extensions-page";

const mocks = vi.hoisted(() => ({
  listNovels: vi.fn(),
  listSkills: vi.fn(),
  listSkillVersions: vi.fn(),
  getToolCatalog: vi.fn(),
  listToolConnectors: vi.fn(),
}));

vi.mock("@/lib/api/extensions", () => ({
  extensionsApi: {
    listNovels: mocks.listNovels,
    listSkills: mocks.listSkills,
    listSkillVersions: mocks.listSkillVersions,
    getToolCatalog: mocks.getToolCatalog,
    listToolConnectors: mocks.listToolConnectors,
  },
}));

const version = {
  id: 11,
  registry_id: 1,
  owner_id: 2,
  novel_id: 7,
  name: "已有技能",
  version: "1.0.0",
  prompt: "已有",
  input_schema: {},
  output_schema: {},
  allowed_tools: ["get_chapter"],
  budget: {},
  status: "active",
  execution_status: "declarative_only",
  runtime_note: "",
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listNovels.mockResolvedValue({
    data: { items: [{ id: 7, title: "测试小说" }] },
  });
  mocks.listSkills.mockResolvedValue({
    data: {
      items: [{ id: 1, novel_id: 7, name: "已有技能", status: "active" }],
    },
  });
  mocks.listSkillVersions.mockResolvedValue({ data: { items: [version] } });
  mocks.getToolCatalog.mockResolvedValue({
    data: {
      items: [
        {
          name: "get_chapter",
          category: "read",
          approval_required: false,
          user_configurable: true,
        },
        {
          name: "publish_illustration",
          category: "candidate",
          approval_required: true,
          user_configurable: true,
        },
      ],
    },
  });
  mocks.listToolConnectors.mockResolvedValue({ data: { items: [], total: 0 } });
});

describe("ExtensionsPage", () => {
  it("展示真实小说、Skill registry、catalog 与声明式注册表单", async () => {
    render(<ExtensionsPage />);

    await waitFor(() => expect(screen.getAllByText("测试小说").length).toBeGreaterThan(0));
    expect(screen.getByText("已有技能")).toBeInTheDocument();
    expect(screen.getByText("get_chapter")).toBeInTheDocument();
    expect(screen.getByText("publish_illustration")).toBeInTheDocument();
    expect(screen.queryByText(/这里只注册声明式 Skill/)).not.toBeInTheDocument();
    expect(screen.queryByText(/只有下列 23 个后端声明的工具/)).not.toBeInTheDocument();
    expect(screen.queryByText(/受限 HTTP Tool：仅支持 HTTPS/)).not.toBeInTheDocument();
    expect(screen.queryByText("任意代码 / Shell：禁止")).not.toBeInTheDocument();
    expect(screen.queryByText(/Tool 版本 owner-scoped/)).not.toBeInTheDocument();
    expect(screen.queryByText(/预算默认：最多 3 次工具调用/)).not.toBeInTheDocument();
    expect(screen.queryByText(/运行边界：declarative_only/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "受限 HTTPS Tools" })).toBeInTheDocument();
    expect(screen.getByLabelText("Tool 名称")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "注册声明式 Skill" })).toBeInTheDocument();
    expect(screen.getByLabelText("Prompt 正文")).toBeInTheDocument();
  });

  it("同名 Skill registry 只加载一次版本列表，避免重复 React key", async () => {
    mocks.listSkills.mockResolvedValue({
      data: {
        items: [
          { id: 1, novel_id: 7, name: "已有技能", status: "active" },
          { id: 2, novel_id: 8, name: "已有技能", status: "active" },
          { id: 3, novel_id: 9, name: "已有技能", status: "active" },
        ],
      },
    });

    render(<ExtensionsPage />);

    await waitFor(() => expect(screen.getByText("已有技能 v1.0.0")).toBeInTheDocument());
    expect(mocks.listSkillVersions).toHaveBeenCalledTimes(1);
    expect(mocks.listSkillVersions).toHaveBeenCalledWith("已有技能");
    expect(screen.getAllByText("已有技能 v1.0.0")).toHaveLength(1);
  });
});
