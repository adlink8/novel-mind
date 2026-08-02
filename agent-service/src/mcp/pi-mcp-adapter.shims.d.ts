/**
 * pi-mcp-adapter 类型桥（Pitfall 7 决策，25.3-03）。
 *
 * pi-mcp-adapter 是 TS-source 包：exports 直接指向 .ts 源码，包内 import 使用
 * `.ts` 扩展名。直接把该包拉进 `tsc --noEmit` 程序会触发 TS5097 与整包类型检查
 * 负担（见 qualification/pi-mcp-adapter.md「Pitfall 7 决策」）。本 ambient 模块
 * 声明只覆盖本 spike 实际消费的 API 表面；运行时仍由 vitest/Node 解析真实包
 * （单元测试以 `vi.mock("pi-mcp-adapter")` 隔离）。
 *
 * 若上游包面变化，本桥与 createMcpAdapter 调用方需要同步更新。
 */
declare module "pi-mcp-adapter" {
  export type ToolPrefix = "server" | "none" | "short" | "mcp";
  export type HostConfigDiscovery = "off" | "prompt" | "on";
  export type ServerLifecycle = "keep-alive" | "lazy" | "lazy-keep-alive" | "eager";

  /** 单个 MCP 服务器的 ServerEntry（只列隔离接线用到的表面）。 */
  export interface ServerEntry {
    command?: string;
    args?: string[];
    url?: string;
    env?: Record<string, string>;
    auth?: "oauth" | "bearer" | false;
    lifecycle?: ServerLifecycle;
    requestTimeoutMs?: number;
    exposeResources?: boolean;
    directTools?: boolean | string[];
    disabled?: boolean;
  }

  export interface McpSettings {
    toolPrefix?: ToolPrefix;
    hostConfigDiscovery?: HostConfigDiscovery;
    requestTimeoutMs?: number;
    directTools?: boolean;
    disableProxyTool?: boolean;
    autoAuth?: boolean;
  }

  export interface McpConfig {
    mcpServers: Record<string, ServerEntry>;
    settings?: McpSettings;
  }

  export interface McpAdapterOptions {
    config?: McpConfig;
    configPath?: string;
  }

  /** createMcpAdapter({ config }) 返回 Pi 扩展安装函数（ExtensionAPI -> void）。 */
  export function createMcpAdapter(options?: McpAdapterOptions): (pi: unknown) => void;
}
