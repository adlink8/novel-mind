import { describe, it, expect } from "vitest";
import { cn } from "./utils";

describe("cn()", () => {
  it("合并多个类名为字符串", () => {
    expect(cn("p-2", "m-2")).toBe("p-2 m-2");
  });

  it("条件类名为 false 时不包含", () => {
    const condition = false;
    expect(cn("base", condition && "hidden")).toBe("base");
  });

  it("条件类名为 true 时包含", () => {
    const condition = true;
    expect(cn("base", condition && "hidden")).toBe("base hidden");
  });

  it("Tailwind 冲突时后者覆盖前者", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
  });

  it("处理对象形式的条件类名", () => {
    expect(cn("base", { active: true, disabled: false })).toBe("base active");
  });

  it("处理数组形式的类名", () => {
    expect(cn(["a", "b"], "c")).toBe("a b c");
  });

  it("过滤 falsy 值", () => {
    expect(cn("a", null, undefined, false, "b")).toBe("a b");
  });
});
