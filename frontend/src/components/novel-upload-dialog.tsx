"use client";

import React, { useCallback, useState, useRef } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogTrigger,
  DialogClose,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { novelsApi, type Novel } from "@/lib/api";

interface NovelUploadDialogProps {
  children: React.ReactNode;
  onUploadComplete?: (novel: Novel) => void;
}

type UploadStatus = "idle" | "uploading" | "success" | "error";

export function NovelUploadDialog({
  children,
  onUploadComplete,
}: NovelUploadDialogProps) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<UploadStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [errorMsg, setErrorMsg] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const reset = useCallback(() => {
    setFile(null);
    setStatus("idle");
    setProgress(0);
    setErrorMsg("");
    setDragOver(false);
  }, []);

  const validateFile = useCallback((f: File): boolean => {
    if (!f.name.toLowerCase().endsWith(".txt")) {
      setErrorMsg("\u53EA\u652F\u6301 .txt \u683C\u5F0F\u7684\u6587\u4EF6");
      setStatus("error");
      return false;
    }
    if (f.size > 50 * 1024 * 1024) {
      setErrorMsg("\u6587\u4EF6\u5927\u5C0F\u4E0D\u80FD\u8D85\u8FC7 50MB");
      setStatus("error");
      return false;
    }
    return true;
  }, []);

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

  const handleUpload = useCallback(async () => {
    if (!file) return;
    setStatus("uploading");
    setProgress(0);

    // Simulate progress
    const progressInterval = setInterval(() => {
      setProgress((prev) => {
        if (prev >= 90) {
          clearInterval(progressInterval);
          return 90;
        }
        return prev + Math.random() * 15;
      });
    }, 300);

    try {
      const res = await novelsApi.upload(file);
      clearInterval(progressInterval);
      setProgress(100);
      setStatus("success");
      onUploadComplete?.(res.data);
      setTimeout(() => {
        setOpen(false);
        reset();
      }, 1000);
    } catch (err) {
      clearInterval(progressInterval);
      const message =
        err instanceof Error ? err.message : "\u4E0A\u4F20\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5";
      setErrorMsg(message);
      setStatus("error");
      setProgress(0);
    }
  }, [file, onUploadComplete, reset]);

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen) {
        reset();
      }
      setOpen(nextOpen);
    },
    [reset]
  );

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger render={children as React.ReactElement} />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{"\u5BFC\u5165\u5C0F\u8BF4"}</DialogTitle>
          <DialogDescription>
            {"\u4E0A\u4F20 TXT \u6587\u4EF6\uFF0CAI \u5C06\u81EA\u52A8\u89E3\u6790\u5C0F\u8BF4\u5185\u5BB9"}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Drop zone */}
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
              {file ? "\uD83D\uDCC4" : "\uD83D\uDCC1"}
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
                <p className="text-sm font-medium">
                  {"\u62D6\u62FD\u6587\u4EF6\u5230\u8FD9\u91CC\uFF0C\u6216\u70B9\u51FB\u9009\u62E9\u6587\u4EF6"}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {"\u652F\u6301 .txt \u683C\u5F0F\uFF0C\u6700\u5927 50MB"}
                </p>
              </div>
            )}
          </div>

          {/* Progress bar */}
          {status === "uploading" && (
            <div className="space-y-2">
              <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full bg-novel-500 rounded-full transition-all duration-300"
                  style={{ width: `${Math.min(progress, 100)}%` }}
                />
              </div>
              <p className="text-xs text-center text-muted-foreground">
                {"\u4E0A\u4F20\u4E2D..."} {Math.round(progress)}%
              </p>
            </div>
          )}

          {/* Success state */}
          {status === "success" && (
            <div className="flex items-center justify-center gap-2 rounded-lg bg-green-50 border border-green-200 p-3 text-sm text-green-700">
              <span>{"\u2705"}</span>
              <span>{"\u4E0A\u4F20\u6210\u529F\uFF01\u6B63\u5728\u89E3\u6790\u5C0F\u8BF4..."}</span>
            </div>
          )}

          {/* Error state */}
          {status === "error" && errorMsg && (
            <div className="flex items-center justify-center gap-2 rounded-lg bg-red-50 border border-red-200 p-3 text-sm text-red-700">
              <span>{"\u274C"}</span>
              <span>{errorMsg}</span>
            </div>
          )}

          {/* Action buttons */}
          <div className="flex justify-end gap-2">
            <DialogClose>
              <Button variant="outline">{"\u53D6\u6D88"}</Button>
            </DialogClose>
            <Button
              onClick={handleUpload}
              disabled={!file || status === "uploading"}
            >
              {status === "uploading"
                ? "\u4E0A\u4F20\u4E2D..."
                : "\u5F00\u59CB\u4E0A\u4F20"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
