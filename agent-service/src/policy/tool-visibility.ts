/**
 * restrict-only 工具可见性过滤（25.3-04 / D-11 / RESEARCH Pattern 4）。
 *
 * 语义（pi-permission-system 文档化）：
 *  - `filterToolSet` 只从活动工具集**移除** denied/disabled 条目——输出永远是
 *    输入的子集，工具面只会缩小，绝不会凭空增加。
 *  - `assertKnownTools` 在权限检查**之前**阻断未注册工具（与 25.3-02
 *    ToolRegistryManifest 的 registryNames 对照）：未注册 → 抛错（fail-closed）。
 */

/** 过滤：返回 activeTools 中不在 deniedOrDisabled 集合里的子集（restrict-only）。 */
export function filterToolSet(
  activeTools: readonly string[],
  deniedOrDisabled: ReadonlySet<string>,
): string[] {
  return activeTools.filter((tool) => !deniedOrDisabled.has(tool));
}

/**
 * 断言工具都在 registryNames 内（ToolRegistryManifest 单一事实源，25.3-02）。
 * 任一工具未注册 → 抛错，供权限检查前的 fail-closed 阻断（T-25.3-04-01）。
 */
export function assertKnownTools(
  tools: Iterable<string>,
  registryNames: ReadonlySet<string> | readonly string[],
): void {
  const known = registryNames instanceof Set ? registryNames : new Set(registryNames);
  for (const tool of tools) {
    if (!known.has(tool)) {
      throw new Error(`工具不在 ToolRegistryManifest 中（fail-closed）: ${tool}`);
    }
  }
}
