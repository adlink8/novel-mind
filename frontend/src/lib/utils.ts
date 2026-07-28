/**
 * 工具函数模块
 *
 * 提供 Tailwind CSS 类名合并工具:
 * - cn(): 合并多个类名，自动处理冲突（如 "p-2 p-4" 只保留 "p-4"）
 *
 * 依赖:
 * - clsx: 条件类名拼接
 * - tailwind-merge: 智能合并 Tailwind 类（解决冲突）
 */

import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

/**
 * 合并 Tailwind CSS 类名（智能去重 + 冲突解决）
 *
 * @param inputs - 类名列表（支持条件表达式、对象、数组）
 * @returns 合并后的类名字符串
 *
 * @example
 * cn("p-2", "p-4")           // => "p-4"（后者覆盖前者）
 * cn("text-red-500", condition && "text-blue-500")  // 条件切换
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
