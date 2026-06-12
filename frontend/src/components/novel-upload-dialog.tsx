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

interface NovelUploadDialogProps {
  children: React.ReactNode;
  onUploadComplete?: (novel: NovelUploadResponse) => void;
}

type UploadStatus = "idle" | "uploading" | "success" | "error";

/** 导入阶段中文映射 */
const STAGE_LABELS: Record<string, string> = {
  uploading: "正在接收文件...",
  detecting: "正在检测编码...",
  parsing: "正在解析章节...",
  saving: "正在保存到数据库...",
  ready: "导入完成",
  error: "导入失败",
  unknown: "处理中...",
};

export function NovelUploadDialog({
  children,
  onUploadComplete,
}: NovelUploadDialogProps) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<UploadStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [stageMessage, setStageMessage] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [novelId, setNovelId] = useState<string | null>(null);
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
    setFile(null);
    setStatus("idle");
    setProgress(0);
    setStageMessage("");
    setErrorMsg("");
    setDragOver(false);
    setNovelId(null);
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
    (f: File) => {
      setErrorMsg("");
      setStatus("idle");
      if (validateFile(f)) {
        setFile(f);
      }
    },
    [validateFile]
  );

  /** 拖拽放下处理 */
  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const droppedFile = e.dataTransfer.files[0];
      if (droppedFile) {
        handleFileSelect(droppedFile);
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
      const selected = e.target.files?.[0];
      if (selected) {
        handleFileSelect(selected);
      }
    },
    [handleFileSelect]
  );

  /** 轮询导入进度 */
  const startPolling = useCallback((id: string) => {
    clearPollTimer();
    pollTimerRef.current = setInterval(async () => {
      try {
        const res = await novelsApi.getImportStatus(id);
        const data = res.data;
        setProgress(data.percent);
        setStageMessage(STAGE_LABELS[data.stage] || data.message || "处理中...");

        if (data.stage === "ready" || data.stage === "error") {
          clearPollTimer();
          if (data.stage === "ready") {
            setStatus("success");
            setTimeout(() => {
              setOpen(false);
              reset();
            }, 800);
          }
        }
      } catch {
        // 轮询失败不中断，继续尝试
      }
    }, 500);
  }, [clearPollTimer, reset]);

  /** 执行上传 */
  const handleUpload = useCallback(async () => {
    if (!file) return;
    setStatus("uploading");
    setProgress(5);
    setStageMessage(STAGE_LABELS.uploading);

    try {
      const res = await novelsApi.upload(file);
      const data = res.data;
      setNovelId(String(data.id));
      setProgress(50);
      setStageMessage(STAGE_LABELS.parsing);

      // 上传完成后开始轮询后端实际进度
      startPolling(String(data.id));
      onUploadComplete?.(data);
    } catch (err) {
      clearPollTimer();
      const message = err instanceof Error ? err.message : "上传失败，请重试";
      setErrorMsg(message);
      setStatus("error");
      setProgress(0);
    }
  }, [file, onUploadComplete, clearPollTimer, startPolling]);

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
      <DialogContent className="sm:max-w-md">
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
              relative flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-8 cursor-pointer transition-colors
              ${
                dragOver
                  ? "border-novel-500 bg-novel-50"
                  : "border-border bg-muted/30 hover:border-novel-400 hover:bg-novel-50/50"
              }
            `}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".txt"
              onChange={handleInputChange}
              className="hidden"
            />
            <div className="text-4xl mb-3">
              {file ? "📄" : "📁"}
            </div>
            {file ? (
              <div className="text-center">
                <p className="font-medium text-sm">{file.name}</p>
                <p className="text-xs text-muted-foreground mt-1">
                  {(file.size / 1024 / 1024).toFixed(2)} MB
                </p>
              </div>
            ) : (
              <div className="text-center">
                <p className="text-sm font-medium">{"拖拽文件到这里，或点击选择文件"}</p>
                <p className="text-xs text-muted-foreground mt-1">{"支持 .txt 格式，最大 50MB（自动检测 UTF-8 / GBK / Big5 等编码）"}</p>
              </div>
            )}
          </div>

          {/* 进度条 */}
          {status === "uploading" && (
            <div className="space-y-2">
              <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full bg-novel-500 rounded-full transition-all duration-300"
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
            <div className="flex items-center justify-center gap-2 rounded-lg bg-green-50 border border-green-200 p-3 text-sm text-green-700">
              <span>{"✅"}</span>
              <span>{"导入成功！"}</span>
            </div>
          )}

          {/* 错误状态 */}
          {status === "error" && errorMsg && (
            <div className="flex items-center justify-center gap-2 rounded-lg bg-red-50 border border-red-200 p-3 text-sm text-red-700">
              <span>{"❌"}</span>
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
              disabled={!file || status === "uploading"}
            >
              {status === "uploading" ? "处理中..." : "开始上传"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
