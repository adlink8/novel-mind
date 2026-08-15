import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NovelUploadDialog } from "./novel-upload-dialog";

const mocks = vi.hoisted(() => ({
  upload: vi.fn(),
  getImportJobStatus: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    novelsApi: {
      upload: mocks.upload,
      getImportJobStatus: mocks.getImportJobStatus,
    },
  };
});

function makeFile(name: string, size = 100): File {
  return new File(["x".repeat(size)], name, { type: "text/plain" });
}

async function openDialog() {
  render(
    <NovelUploadDialog onUploadComplete={vi.fn()}>
      <button type="button" data-testid="upload-trigger">
        导入
      </button>
    </NovelUploadDialog>
  );
  fireEvent.click(screen.getByTestId("upload-trigger"));
  await screen.findByText("导入小说");
}

describe("NovelUploadDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.upload.mockResolvedValue({
      data: { job_id: 9, id: 9, novel_id: null, title: "", status: "pending" },
    });
    mocks.getImportJobStatus.mockResolvedValue({
      data: {
        job_id: 9,
        novel_id: 1,
        stage: "ready",
        percent: 100,
        message: "导入完成",
      },
    });
  });

  it("打开对话框展示标题与拖拽提示", async () => {
    await openDialog();
    expect(screen.getByText("上传 TXT 文件，AI 将自动解析小说内容")).toBeInTheDocument();
    expect(screen.getByText(/拖拽多个文件到这里/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始上传" })).toBeDisabled();
  });

  it("选择 .txt 文件后显示文件名并可上传", async () => {
    await openDialog();
    // input 在 dropzone 内、不可见；直接通过文件输入选择
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(fileInput, { target: { files: [makeFile("第一章.txt")] } });
    expect(screen.getByText("已选择 1 个文件")).toBeInTheDocument();
    expect(screen.getByText("第一章.txt")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始上传" }));
    await waitFor(() => expect(mocks.upload).toHaveBeenCalled());
    await screen.findByText("导入成功！");
  });

  it("拒绝非 .txt 文件并展示错误", async () => {
    await openDialog();
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(fileInput, {
      target: { files: [makeFile("novel.pdf")] },
    });
    expect(screen.getByText("仅支持 .txt 格式的文件")).toBeInTheDocument();
  });

  it("拒绝超过 50MB 的文件并展示错误", async () => {
    await openDialog();
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(fileInput, {
      target: { files: [makeFile("big.txt", 51 * 1024 * 1024)] },
    });
    expect(screen.getByText("文件大小不能超过 50MB")).toBeInTheDocument();
  });

  it("上传失败展示错误信息", async () => {
    mocks.upload.mockRejectedValue(new Error("网络中断"));
    await openDialog();
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(fileInput, { target: { files: [makeFile("a.txt")] } });
    fireEvent.click(screen.getByRole("button", { name: "开始上传" }));
    await screen.findByText("网络中断");
  });

  it("导入作业失败时展示失败消息", async () => {
    mocks.getImportJobStatus.mockResolvedValue({
      data: {
        job_id: 9,
        novel_id: null,
        stage: "failed",
        percent: 10,
        message: "解析失败",
      },
    });
    await openDialog();
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(fileInput, { target: { files: [makeFile("a.txt")] } });
    fireEvent.click(screen.getByRole("button", { name: "开始上传" }));
    await screen.findByText("解析失败");
  });

  it("拖拽文件触发选择", async () => {
    await openDialog();
    const dropzone = screen.getByText(/拖拽多个文件到这里/).closest("div")!;
    fireEvent.dragOver(dropzone);
    fireEvent.drop(dropzone, { dataTransfer: { files: [makeFile("drop.txt")] } });
    expect(screen.getByText("已选择 1 个文件")).toBeInTheDocument();
  });
});
