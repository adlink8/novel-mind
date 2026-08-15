// agent-service 开发启动入口：调用编译产物（dist/src/server.js）中的 startServer。
// 先 `npx tsc` 编译，再 `node start.mjs`。
import { startServer } from "./dist/src/server.js";

const server = await startServer();
const address = server.address();
console.log(
  `[agent-service] ready on http://127.0.0.1:${typeof address === "object" && address ? address.port : "?"}`
);
