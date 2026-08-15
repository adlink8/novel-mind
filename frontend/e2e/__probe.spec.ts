// 临时探针（环境诊断用，已弃用）：safe-delete 配额限制本回合无法删除，先跳过。
import { test } from "@playwright/test";

test.skip("probe (disabled)", () => {});
