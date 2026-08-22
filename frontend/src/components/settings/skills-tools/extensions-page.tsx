"use client";

import { useEffect, useState } from "react";

import {
  extensionsApi,
  type SkillRegisterPayload,
  type SkillStatus,
  type SkillVersion,
  type SkillRegistryItem,
  type ToolCapability,
  type ToolConnector,
  type ToolConnectorPayload,
} from "@/lib/api/extensions";

const EMPTY_FORM: SkillRegisterPayload = {
  novel_id: 0,
  name: "",
  version: "1.0.0",
  description: "",
  prompt: "",
  input_schema: { type: "object" },
  output_schema: { type: "object" },
  allowed_tools: [],
  budget: { max_tool_calls: 3, max_tokens: 1000 },
};

const statusLabels: Record<SkillStatus, string> = {
  draft: "禁用 / 草稿",
  active: "启用",
  deprecated: "已弃用",
};

const EMPTY_CONNECTOR: ToolConnectorPayload = {
  name: "",
  description: "",
  base_url: "https://",
  path: "/",
  method: "GET",
  request_schema: { type: "object", additionalProperties: false },
  response_schema: { type: "object" },
  enabled: false,
};

export function ExtensionsPage() {
  const [novels, setNovels] = useState<{ id: number; title: string }[]>([]);
  const [skills, setSkills] = useState<SkillRegistryItem[]>([]);
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [catalog, setCatalog] = useState<ToolCapability[]>([]);
  const [connectors, setConnectors] = useState<ToolConnector[]>([]);
  const [connectorForm, setConnectorForm] = useState<ToolConnectorPayload>(EMPTY_CONNECTOR);
  const [connectorRequest, setConnectorRequest] = useState("{}");
  const [form, setForm] = useState<SkillRegisterPayload>(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    void Promise.all([
      extensionsApi.listNovels(),
      extensionsApi.listSkills(),
      extensionsApi.getToolCatalog(),
      extensionsApi.listToolConnectors(),
    ])
      .then(async ([novelResponse, skillResponse, catalogResponse, connectorResponse]) => {
        if (!active) return;
        const nextSkills = skillResponse.data.items;
        setNovels(novelResponse.data.items.map((novel) => ({ id: novel.id, title: novel.title })));
        setSkills(nextSkills);
        setCatalog(catalogResponse.data.items);
        setConnectors(connectorResponse.data.items);
        setForm((current) => ({
          ...current,
          novel_id: current.novel_id || novelResponse.data.items[0]?.id || 0,
        }));
        // Registry rows are novel-scoped, so the same built-in skill name can
        // appear once per novel. The versions endpoint is name-scoped and
        // already returns every matching registry version for the owner;
        // requesting it once per registry multiplies the same rows and creates
        // duplicate React keys. Fetch each distinct name exactly once.
        const skillNames = [...new Set(nextSkills.map((skill) => skill.name))];
        const versionResponses = await Promise.all(
          skillNames.map((skillName) => extensionsApi.listSkillVersions(skillName)),
        );
        if (active) setVersions(versionResponses.flatMap((response) => response.data.items));
      })
      .catch(() => {
        if (active) setMessage("Skills/Tools 加载失败，请稍后再试");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const createConnector = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setMessage(null);
    try {
      const response = await extensionsApi.createToolConnector(connectorForm);
      setConnectors((current) => [response.data, ...current]);
      setConnectorForm(EMPTY_CONNECTOR);
      setMessage("受限 HTTPS Tool 已保存为 draft；请先验证，再启用。");
    } catch {
      setMessage("Tool 保存失败：请检查 HTTPS、路径和 JSON schema。");
    } finally {
      setSaving(false);
    }
  };

  const validateConnector = async (connector: ToolConnector) => {
    try {
      const response = await extensionsApi.validateToolConnector(connector.id);
      setConnectors((current) => current.map((item) => item.id === connector.id ? response.data : item));
      setMessage(`${connector.name} 已 validated；运行接线仍未启用。`);
    } catch {
      setMessage("Tool 验证失败。");
    }
  };

  const dryRunConnector = async (connector: ToolConnector) => {
    try {
      const request = JSON.parse(connectorRequest) as Record<string, unknown>;
      const response = await extensionsApi.dryRunToolConnector(connector.id, request);
      setMessage(`${connector.name} dry-run 返回 HTTP ${response.data.status_code}（HTTP adapter fake）。`);
    } catch {
      setMessage("dry-run 失败：只能运行 active connector，且请求必须是合法 JSON。");
    }
  };

  const toggleTool = (name: string) => {
    setForm((current) => ({
      ...current,
      allowed_tools: current.allowed_tools.includes(name)
        ? current.allowed_tools.filter((tool) => tool !== name)
        : [...current.allowed_tools, name],
    }));
  };

  const createSkill = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setMessage(null);
    try {
      const response = await extensionsApi.createSkill(form);
      setVersions((current) => [response.data, ...current]);
      setSkills((current) =>
        current.some(
          (skill) =>
            skill.name === response.data.name &&
            skill.novel_id === response.data.novel_id,
        )
          ? current
          : [
              ...current,
              {
                id: response.data.registry_id,
                owner_id: response.data.owner_id,
                novel_id: response.data.novel_id,
                name: response.data.name,
                description: response.data.description,
                status: response.data.status,
              },
            ],
      );
      setMessage("声明式 Skill 已注册");
    } catch {
      setMessage("注册失败，请检查小说、版本和 Tool 选择");
    } finally {
      setSaving(false);
    }
  };

  const updateStatus = async (version: SkillVersion, status: SkillStatus) => {
    try {
      const response = await extensionsApi.updateSkillVersionStatus(version.name, version.id, status);
      setVersions((current) => current.map((item) => (item.id === version.id ? response.data : item)));
      setSkills((current) =>
        current.map((skill) =>
          skill.id === version.registry_id ? { ...skill, status } : skill,
        ),
      );
    } catch {
      setMessage("状态更新失败，请稍后再试");
    }
  };

  return (
    <main className="mx-auto max-w-6xl space-y-8 px-5 py-8 sm:px-8">
      <header>
        <h1 className="font-serif text-3xl font-semibold">Skills / Tools 管理</h1>
      </header>

      {loading ? <p role="status">加载中…</p> : null}
      {message ? <p role="status" className="text-sm text-muted-foreground">{message}</p> : null}

      <section className="grid gap-6 lg:grid-cols-[0.85fr_1.15fr]">
        <div className="space-y-4">
          <section className="paper-surface rounded-3xl p-5">
            <h2 className="font-serif text-xl font-semibold">小说</h2>
            <ul className="mt-3 space-y-2 text-sm">
              {novels.map((novel) => <li key={novel.id}>{novel.title}</li>)}
            </ul>
          </section>
          <section className="paper-surface rounded-3xl p-5">
            <h2 className="font-serif text-xl font-semibold">现有 Skill registry</h2>
            <ul className="mt-3 space-y-3 text-sm">
              {skills.map((skill) => (
                <li key={skill.id} className="flex items-center justify-between gap-3">
                  <span>{skill.name}</span>
                  <span className="text-muted-foreground">{statusLabels[skill.status]}</span>
                </li>
              ))}
            </ul>
            {versions.length > 0 ? (
              <div className="mt-4 space-y-3 border-t border-border/70 pt-4">
                {versions.map((version) => (
                  <label key={version.id} className="flex items-center justify-between gap-3 text-sm">
                    <span>{version.name} v{version.version}</span>
                    <select
                      aria-label={`${version.name} v${version.version} 状态`}
                      value={version.status}
                      onChange={(event) => void updateStatus(version, event.target.value as SkillStatus)}
                      className="rounded-lg border border-input bg-background px-2 py-1"
                    >
                      {Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                  </label>
                ))}
              </div>
            ) : null}
          </section>
        </div>

        <section className="paper-surface rounded-3xl p-5">
          <h2 className="font-serif text-xl font-semibold">Tool Capability Catalog</h2>
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {catalog.map((tool) => (
              <label key={tool.name} className="flex items-start gap-2 rounded-xl border border-border/70 p-3 text-sm">
                <input
                  type="checkbox"
                  checked={form.allowed_tools.includes(tool.name)}
                  disabled={!tool.user_configurable}
                  onChange={() => toggleTool(tool.name)}
                />
                <span>
                  <span className="block font-medium">{tool.name}</span>
                  <span className="text-xs text-muted-foreground">{tool.category}{tool.approval_required ? " · 需要审批" : ""}</span>
                </span>
              </label>
            ))}
          </div>
        </section>
      </section>

      <section className="paper-surface space-y-5 rounded-3xl p-5">
        <h2 className="font-serif text-xl font-semibold">受限 HTTPS Tools</h2>
        <form onSubmit={(event) => void createConnector(event)} className="grid gap-3 sm:grid-cols-2">
          <input aria-label="Tool 名称" required placeholder="名称" value={connectorForm.name} onChange={(event) => setConnectorForm({ ...connectorForm, name: event.target.value })} className="h-9 rounded-lg border border-input bg-background px-2.5" />
          <input aria-label="Tool 描述" placeholder="描述" value={connectorForm.description ?? ""} onChange={(event) => setConnectorForm({ ...connectorForm, description: event.target.value })} className="h-9 rounded-lg border border-input bg-background px-2.5" />
          <input aria-label="Tool Base URL" required type="url" placeholder="https://api.example.com" value={connectorForm.base_url} onChange={(event) => setConnectorForm({ ...connectorForm, base_url: event.target.value })} className="h-9 rounded-lg border border-input bg-background px-2.5" />
          <input aria-label="Tool Path" required placeholder="/v1/resource" value={connectorForm.path} onChange={(event) => setConnectorForm({ ...connectorForm, path: event.target.value })} className="h-9 rounded-lg border border-input bg-background px-2.5" />
          <select aria-label="Tool Method" value={connectorForm.method} onChange={(event) => setConnectorForm({ ...connectorForm, method: event.target.value as "GET" | "POST" })} className="h-9 rounded-lg border border-input bg-background px-2.5">
            <option value="GET">GET</option><option value="POST">POST</option>
          </select>
          <button type="submit" disabled={saving} className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">保存 draft</button>
        </form>
        <label className="block space-y-2 text-sm">dry-run 请求 JSON
          <textarea aria-label="dry-run 请求 JSON" value={connectorRequest} onChange={(event) => setConnectorRequest(event.target.value)} className="min-h-20 w-full rounded-lg border border-input bg-background p-2.5 font-mono text-xs" />
        </label>
        <ul className="space-y-3 text-sm">
          {connectors.map((connector) => (
            <li key={connector.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border/70 p-3">
              <span><strong>{connector.name}</strong> · v{connector.version} · {connector.status}</span>
              <span className="flex gap-2">
                {connector.status === "draft" ? <button type="button" onClick={() => void validateConnector(connector)} className="rounded-lg border border-input px-2 py-1">验证</button> : null}
                {connector.status === "active" ? <button type="button" onClick={() => void dryRunConnector(connector)} className="rounded-lg border border-input px-2 py-1">dry-run</button> : null}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <form onSubmit={(event) => void createSkill(event)} className="paper-surface space-y-5 rounded-3xl p-5">
        <h2 className="font-serif text-xl font-semibold">注册声明式 Skill</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="space-y-2 text-sm">小说<select aria-label="小说" value={form.novel_id} onChange={(event) => setForm({ ...form, novel_id: Number(event.target.value) })} className="h-9 w-full rounded-lg border border-input bg-background px-2.5">{novels.map((novel) => <option key={novel.id} value={novel.id}>{novel.title}</option>)}</select></label>
          <label className="space-y-2 text-sm">名称<input aria-label="Skill 名称" required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} className="h-9 w-full rounded-lg border border-input bg-background px-2.5" /></label>
          <label className="space-y-2 text-sm">版本<input aria-label="版本" required pattern="\\d+\\.\\d+\\.\\d+" value={form.version} onChange={(event) => setForm({ ...form, version: event.target.value })} className="h-9 w-full rounded-lg border border-input bg-background px-2.5" /></label>
          <label className="space-y-2 text-sm">描述<input aria-label="描述" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} className="h-9 w-full rounded-lg border border-input bg-background px-2.5" /></label>
        </div>
        <label className="block space-y-2 text-sm">Prompt 正文<textarea aria-label="Prompt 正文" required value={form.prompt} onChange={(event) => setForm({ ...form, prompt: event.target.value })} className="min-h-28 w-full rounded-lg border border-input bg-background p-2.5" /></label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="space-y-2 text-sm">输入 schema（JSON）<textarea aria-label="输入 schema" value={JSON.stringify(form.input_schema, null, 2)} onChange={(event) => { try { setForm({ ...form, input_schema: JSON.parse(event.target.value) }); } catch { /* keep editable text until valid JSON */ } }} className="min-h-24 w-full rounded-lg border border-input bg-background p-2.5 font-mono text-xs" /></label>
          <label className="space-y-2 text-sm">输出 schema（JSON）<textarea aria-label="输出 schema" value={JSON.stringify(form.output_schema, null, 2)} onChange={(event) => { try { setForm({ ...form, output_schema: JSON.parse(event.target.value) }); } catch { /* keep editable text until valid JSON */ } }} className="min-h-24 w-full rounded-lg border border-input bg-background p-2.5 font-mono text-xs" /></label>
        </div>
        <button type="submit" disabled={saving || !form.novel_id || !form.allowed_tools.length} className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50">{saving ? "注册中…" : "注册 Skill 版本"}</button>
      </form>
    </main>
  );
}
