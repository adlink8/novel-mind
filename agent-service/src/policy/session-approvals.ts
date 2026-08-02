/**
 * 会话级批准缓存（25.3-04 / D-11 / A5 / ASVS V3）。
 *
 * 按 run 存在 agent-service 内存中的极简 Set：`add(action)` / `has(action)` /
 * `size`。**没有持久化路径**——run 结束即消失，绝不沉淀为常驻权限
 * （T-25.3-04-03：session approval 洗白成 standing permission）。
 */

/** 单 run 会话级批准集合（每 run 构造一个，无持久化路径）。 */
export class SessionApprovals {
  private readonly actions = new Set<string>();

  /** 批准某动作（approved_for_session 决策落地）。 */
  add(action: string): void {
    this.actions.add(action);
  }

  /** 该动作是否已在本 run 内获批。 */
  has(action: string): boolean {
    return this.actions.has(action);
  }

  /** 只读视图：供策略引擎 evaluate 读取；外部不得增删。 */
  asReadonly(): ReadonlySet<string> {
    return this.actions;
  }

  /** 已批准动作数量（测试/诊断用）。 */
  get size(): number {
    return this.actions.size;
  }
}
