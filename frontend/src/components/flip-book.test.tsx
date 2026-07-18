import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FlipBook, type FlipBookPage } from "./flip-book";

const pages: FlipBookPage[] = [
  { id: "p1", front: <div>第一页</div> },
  { id: "p2", front: <div>第二页</div> },
];

function setup() {
  return render(<FlipBook pages={pages} ariaLabel="测试翻页书" />);
}

describe("FlipBook", () => {
  it("初始展开：封面 + 第一叶，页码 1/N+1，上一页不可用", () => {
    setup();
    expect(screen.getByTestId("flip-book-scene")).toHaveAttribute("data-flipped", "0");
    expect(screen.getByTestId("flip-page-indicator")).toHaveTextContent("1 / 3");
    expect(screen.getByTestId("flip-prev-btn")).toBeDisabled();
    expect(screen.getByTestId("flip-next-btn")).toBeEnabled();
    expect(screen.getByTestId("flip-leaf-0")).toHaveAttribute("data-flipped", "false");
  });

  it("点击下一页按钮翻转最上层书叶并更新页码", () => {
    setup();
    fireEvent.click(screen.getByTestId("flip-next-btn"));
    expect(screen.getByTestId("flip-book-scene")).toHaveAttribute("data-flipped", "1");
    expect(screen.getByTestId("flip-leaf-0")).toHaveAttribute("data-flipped", "true");
    expect(screen.getByTestId("flip-page-indicator")).toHaveTextContent("2 / 3");
  });

  it("翻页有界：全部翻完后下一页禁用，可回翻", () => {
    setup();
    fireEvent.click(screen.getByTestId("flip-next-btn"));
    fireEvent.click(screen.getByTestId("flip-next-zone"));
    expect(screen.getByTestId("flip-book-scene")).toHaveAttribute("data-flipped", "2");
    expect(screen.getByTestId("flip-next-btn")).toBeDisabled();
    expect(screen.getByTestId("flip-page-indicator")).toHaveTextContent("3 / 3");

    fireEvent.click(screen.getByTestId("flip-prev-zone"));
    expect(screen.getByTestId("flip-book-scene")).toHaveAttribute("data-flipped", "1");
    expect(screen.getByTestId("flip-leaf-1")).toHaveAttribute("data-flipped", "false");
  });

  it("悬停右页边缘时当前页轻微掀起（翻页预告），离开后复位", () => {
    setup();
    const zone = screen.getByTestId("flip-next-zone");
    fireEvent.pointerEnter(zone);
    expect(screen.getByTestId("flip-leaf-0").style.transform).toContain("-14deg");
    fireEvent.pointerLeave(zone);
    expect(screen.getByTestId("flip-leaf-0").style.transform).toContain("rotateY(0deg)");
  });
});

describe("FlipBook 单页布局", () => {
  it("封面作为首张书叶，页码含封底（1 / N+2）", () => {
    render(
      <FlipBook
        layout="single"
        pages={pages}
        insideCover={<div>封面内容</div>}
        insideBackCover={<div>封底内容</div>}
        ariaLabel="单页书"
      />
    );
    expect(screen.getByText("封面内容")).toBeInTheDocument();
    expect(screen.getByTestId("flip-page-indicator")).toHaveTextContent("1 / 4");
    fireEvent.click(screen.getByTestId("flip-next-btn"));
    expect(screen.getByTestId("flip-leaf-0")).toHaveAttribute("data-flipped", "true");
    expect(screen.getByTestId("flip-page-indicator")).toHaveTextContent("2 / 4");
  });
});

describe("FlipBook 自动翻页", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("按间隔自动翻页，到底后合书回到封面", () => {
    vi.useFakeTimers();
    render(<FlipBook pages={pages} autoFlipMs={5000} ariaLabel="自动翻页书" />);
    expect(screen.getByTestId("flip-book-scene")).toHaveAttribute("data-flipped", "0");
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.getByTestId("flip-book-scene")).toHaveAttribute("data-flipped", "1");
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.getByTestId("flip-book-scene")).toHaveAttribute("data-flipped", "2");
    // 到底后再过一拍 → 回到封面
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.getByTestId("flip-book-scene")).toHaveAttribute("data-flipped", "0");
  });

  it("悬停场景时暂停自动翻页", () => {
    vi.useFakeTimers();
    render(<FlipBook pages={pages} autoFlipMs={5000} ariaLabel="自动翻页书" />);
    const scene = screen.getByTestId("flip-book-scene");
    fireEvent.pointerEnter(scene);
    act(() => vi.advanceTimersByTime(10000));
    expect(scene).toHaveAttribute("data-flipped", "0");
    fireEvent.pointerLeave(scene);
    act(() => vi.advanceTimersByTime(5000));
    expect(scene).toHaveAttribute("data-flipped", "1");
  });
});
