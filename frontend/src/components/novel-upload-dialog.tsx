/**
 * 小说上传对话框组件
 *
 * 支持两种文件选择方式:
 * 1. 拖拽上传（Drag & Drop）
 * 2. 点击选择文件
 *
 * 上传流程:
 * 1. 用户选择/拖入 .txt 文件
 * 2. 前端校验格式和大小（50MB 限制）
 * 3. 模拟进度条（300ms 间隔递增到 90%）
 * 4. 调用 POST /api/novels/upload
 * 5. 成功后回调 onUploadComplete，关闭对话框
 *
 * Props:
 * - children: 触发按钮（通过 DialogTrigger 渲染）
 * - onUploadComplete: 上传成功回调
 */

"use client";

import React, { useCallback, useState, useRef, useEffect } from "react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
  DialogDescription, DialogTrigger, DialogClose,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { novelsApi, type NovelUploadResponse } from "@/lib/api";
import { CheckCircle2, FileText, UploadCloud, XCircle } from "lucide-react";

interface NovelUploadDialogProps {
  children: React.ReactNode;
  onUploadComplete?: (novel: NovelUploadResponse) => void;
}

type UploadStatus = "idle" | "uploading" | "success" | "error";

/** 导入阶段中文映射 */
const STAGE_LABELS: Record<string, string> = {
  pending: "等待处理...",
  uploading: "正在接收文件...",
  detecting: "正在检测编码...",
  parsing: "正在解析章节...",
  saving: "正在保存到数据库...",
  chunking: "正在分块...",
  embedding: "正在建立索引...",
  ready: "导入完成",
  failed: "导入失败",
  error: "导入失败",
  cancelled: "已取消",
  unknown: "处理中...",
};

const TERMINAL_OK = new Set(["ready"]);
const TERMINAL_FAIL = new Set(["failed", "error", "cancelled"]);

export function NovelUploadDialog({
  children,
  onUploadComplete,
}: NovelUploadDialogProps) {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [status, setStatus] = useState<UploadStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [stageMessage, setStageMessage] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /** 清理轮询定时器 */
  const clearPollTimer = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  /** 重置所有状态（关闭对话框时调用） */
  const reset = useCallback(() => {
    setFiles([]);
    setStatus("idle");
    setProgress(0);
    setStageMessage("");
    setErrorMsg("");
    setDragOver(false);
    clearPollTimer();
  }, [clearPollTimer]);

  /** 校验文件格式和大小 */
  const validateFile = useCallback((f: File): boolean => {
    if (!f.name.toLowerCase().endsWith(".txt")) {
      setErrorMsg("仅支持 .txt 格式的文件");
      setStatus("error");
      return false;
    }
    if (f.size > 50 * 1024 * 1024) {
      setErrorMsg("文件大小不能超过 50MB");
      setStatus("error");
      return false;
    }
    return true;
  }, []);

  /** 处理文件选择（点击或拖拽） */
  const handleFileSelect = useCallback(
    (selectedFiles: File[]) => {
      setErrorMsg("");
      setStatus("idle");
      const validFiles = selectedFiles.filter(validateFile);
      if (validFiles.length === selectedFiles.length) {
        setFiles(validFiles);
      }
    },
    [validateFile]
  );

  /** 拖拽放下处理 */
  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const droppedFiles = Array.from(e.dataTransfer.files);
      if (droppedFiles.length > 0) {
        handleFileSelect(droppedFiles);
      }
    },
    [handleFileSelect]
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  }, []);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const selected = Array.from(e.target.files ?? []);
      if (selected.length > 0) {
        handleFileSelect(selected);
      }
    },
    [handleFileSelect]
  );

  /** 按 job_id 轮询导入进度（不是 novel_id） */
  const startPolling = useCallback((jobIds: string[]) => {
    clearPollTimer();
    let ticks = 0;
    pollTimerRef.current = setInterval(async () => {
      ticks += 1;
      try {
        const responses = await Promise.all(
          jobIds.map((jobId) => novelsApi.getImportJobStatus(jobId))
        );
        const jobs = responses.map((response) => response.data);
        const completed = jobs.filter((job) => TERMINAL_OK.has(job.stage)).length;
        const averageProgress = jobs.reduce((sum, job) => sum + (job.percent || 0), 0) / jobs.length;
        setProgress(30 + averageProgress * 0.7);
        setStageMessage(`正在导入：${completed}/${jobs.length} 本已完成`);

        const failed = jobs.find((job) => TERMINAL_FAIL.has(job.stage));
        if (failed) {
          clearPollTimer();
          setStatus("error");
          setErrorMsg(failed.message || "部分文件导入失败，请重试");
          return;
        }

        if (completed === jobs.length) {
          const data = jobs[jobs.length - 1];
          const jobId = jobIds[jobIds.length - 1];
          clearPollTimer();
          setStatus("success");
          setProgress(100);
          setTimeout(() => {
            setOpen(false);
            reset();
            // 通知父组件刷新书架
            onUploadComplete?.({
              id: data.novel_id ?? Number(jobId),
              job_id: Number(jobId),
              novel_id: data.novel_id ?? null,
              title: "",
              status: "ready",
              message: data.message || "导入完成",
              chapter_count: 0,
              word_count: 0,
            });
          }, 800);
          return;
        }

        // 大文件解析可能较久：超过 ~10 分钟仍未结束则提示
        if (ticks > 1200) {
          clearPollTimer();
          setStatus("error");
          setErrorMsg("导入耗时过长，请稍后刷新书架查看是否已完成");
        }
      } catch {
        // 短暂网络错误不中断；持续失败超过阈值仍保留轮询
      }
    }, 500);
  }, [clearPollTimer, reset, onUploadComplete]);

  /** 执行上传 */
  const handleUpload = useCallback(async () => {
    if (files.length === 0) return;
    setStatus("uploading");
    setProgress(5);
    setStageMessage(STAGE_LABELS.uploading);
    setErrorMsg("");

    try {
      const jobIds: string[] = [];
      for (let index = 0; index < files.length; index += 1) {
        setStageMessage(`正在上传第 ${index + 1}/${files.length} 个文件：${files[index].name}`);
        setProgress(Math.max(5, Math.round((index / files.length) * 30)));
        const res = await novelsApi.upload(files[index]);
        const data = res.data;
        jobIds.push(String(data.job_id ?? data.id));
      }
      setProgress(30);
      setStageMessage(`${files.length} 个文件已提交，等待后台导入完成...`);

      startPolling(jobIds);
    } catch (err) {
      clearPollTimer();
      const message = err instanceof Error ? err.message : "上传失败，请重试";
      setErrorMsg(message);
      setStatus("error");
      setProgress(0);
    }
  }, [files, clearPollTimer, startPolling]);

  /** 对话框开关控制（关闭时重置状态） */
  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen) {
        reset();
      }
      setOpen(nextOpen);
    },
    [reset]
  );

  // 组件卸载时清理定时器
  useEffect(() => {
    return () => clearPollTimer();
  }, [clearPollTimer]);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger render={children as React.ReactElement} />
      <DialogContent className="rounded-[28px] sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{"导入小说"}</DialogTitle>
          <DialogDescription>{"上传 TXT 文件，AI 将自动解析小说内容"}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* 拖拽上传区域 */}
          <div
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => inputRef.current?.click()}
            className={`
              relative flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed p-8 transition-colors
              ${
                dragOver
                  ? "border-primary bg-primary/5"
                  : "border-border bg-muted/30 hover:border-primary/50 hover:bg-primary/[0.03]"
              }
            `}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".txt"
              multiple
              onChange={handleInputChange}
              className="hidden"
            />
            <div className="mb-3 grid size-14 place-items-center rounded-2xl bg-secondary text-primary">{files.length > 0 ? <FileText className="size-6" /> : <UploadCloud className="size-6" />}</div>
            {files.length > 0 ? (
              <div className="text-center">
                <p className="font-medium text-sm">已选择 {files.length} 个文件</p>
                <p className="text-xs text-muted-foreground mt-1">
                  {files.slice(0, 3).map((file) => file.name).join("、")}
                  {files.length > 3 ? ` 等 ${files.length} 个文件` : ""}
                </p>
              </div>
            ) : (
              <div className="text-center">
                <p className="text-sm font-medium">{"拖拽多个文件到这里，或点击选择文件"}</p>
                <p className="text-xs text-muted-foreground mt-1">{"支持批量选择 .txt 文件，单个最大 50MB，将按顺序上传"}</p>
              </div>
            )}
          </div>

          {/* 进度条 */}
          {status === "uploading" && (
            <div className="space-y-2">
              <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full rounded-full bg-primary transition-[width] motion-duration-spatial motion-ease-enter"
                  style={{ width: `${Math.min(progress, 100)}%` }}
                />
              </div>
              <p className="text-xs text-center text-muted-foreground">
                {stageMessage || "上传中..."} {Math.round(progress)}%
              </p>
            </div>
          )}

          {/* 成功状态 */}
          {status === "success" && (
            <div className="flex items-center justify-center gap-2 rounded-xl border border-green-200 bg-green-50 p-3 text-sm text-green-700">
              <CheckCircle2 className="size-4" />
              <span>{"导入成功！"}</span>
            </div>
          )}

          {/* 错误状态 */}
          {status === "error" && errorMsg && (
            <div className="flex items-center justify-center gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              <XCircle className="size-4" />
              <span>{errorMsg}</span>
            </div>
          )}

          {/* 操作按钮 */}
          <div className="flex justify-end gap-2">
            <DialogClose render={<Button variant="outline" />}>
              {"取消"}
            </DialogClose>
            <Button
              onClick={handleUpload}
              disabled={files.length === 0 || status === "uploading"}
            >
              {status === "uploading" ? "处理中..." : files.length > 1 ? `导入 ${files.length} 本` : "开始上传"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
