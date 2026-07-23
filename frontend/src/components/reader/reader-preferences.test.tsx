import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useState } from "react";

import {
  DEFAULT_READER_PREFERENCES,
  loadReaderPreferences,
  READER_PREFERENCES_KEY,
  ReaderPreferencesPanel,
  saveReaderPreferences,
  type ReaderPreferences,
} from "./reader-preferences";

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.classList.remove("dark");
  document.documentElement.style.colorScheme = "";
});
afterEach(() => {
  cleanup();
  document.documentElement.classList.remove("dark");
  document.documentElement.style.colorScheme = "";
});

function Harness() {
  const [preferences, setPreferences] = useState<ReaderPreferences>(
    DEFAULT_READER_PREFERENCES
  );
  const [open, setOpen] = useState(true);
  return (
    <ReaderPreferencesPanel
      preferences={preferences}
      onChange={setPreferences}
      open={open}
      onOpenChange={setOpen}
    />
  );
}

describe("reader preferences", () => {
  it("persists preferences and safely restores bounded values", () => {
    saveReaderPreferences({
      ...DEFAULT_READER_PREFERENCES,
      mode: "scroll",
      theme: "dark",
      autoScrollSpeed: 52,
    });
    expect(loadReaderPreferences()).toMatchObject({
      mode: "scroll",
      theme: "dark",
      autoScrollSpeed: 1.75,
    });
    expect(document.documentElement).toHaveClass("dark");

    window.localStorage.setItem(
      READER_PREFERENCES_KEY,
      JSON.stringify({ autoScrollSpeed: 1000, mode: "invalid" })
    );
    expect(loadReaderPreferences()).toMatchObject({
      mode: "paged",
      autoScrollSpeed: 4,
    });
  });

  it("persists typography preferences within bounded ranges", () => {
    saveReaderPreferences({
      ...DEFAULT_READER_PREFERENCES,
      fontSize: 30,
      lineHeight: 9,
      contentWidth: 10,
    });
    expect(loadReaderPreferences()).toMatchObject({
      fontSize: 24,
      lineHeight: 2.6,
      contentWidth: 600,
    });
  });

  it("enables long-page auto scroll and custom background controls", () => {
    render(<Harness />);

    const autoScroll = screen.getByRole("button", { name: "开始" });
    expect(autoScroll).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "长页" }));
    expect(autoScroll).not.toBeDisabled();
    fireEvent.click(autoScroll);
    expect(screen.getByRole("button", { name: "暂停" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );

    fireEvent.click(screen.getByRole("button", { name: "2 倍速" }));
    expect(screen.getByRole("button", { name: "2 倍速" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    fireEvent.click(screen.getByRole("button", { name: "自定义倍速" }));
    expect(screen.getByLabelText("自定义自动下滑倍速")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "自定义" }));
    expect(screen.getByLabelText("自定义阅读背景")).toBeInTheDocument();
  });

  it("exposes typography sliders and a follow-system theme option", () => {
    render(<Harness />);
    expect(screen.getByLabelText("正文字号")).toBeInTheDocument();
    expect(screen.getByLabelText("正文行距")).toBeInTheDocument();
    expect(screen.getByLabelText("正文行宽")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "系统" }));
    expect(screen.getByRole("button", { name: "系统" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  it("offers an explicit immersive mode exit path", async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "进入沉浸模式" }));
    // Exit presence may keep the node briefly; it becomes non-interactive then unmounts.
    await waitFor(() => {
      expect(screen.queryByLabelText("阅读设置")).not.toBeInTheDocument();
    });
  });

  it("closes when clicking outside the settings panel", async () => {
    render(<Harness />);
    expect(screen.getByLabelText("阅读设置")).toBeInTheDocument();
    // Opening-frame suppressOutside clears on the next animation frame.
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => resolve())
    );
    fireEvent.pointerDown(document.body);
    await waitFor(() => {
      expect(screen.queryByLabelText("阅读设置")).not.toBeInTheDocument();
    });
  });
});
