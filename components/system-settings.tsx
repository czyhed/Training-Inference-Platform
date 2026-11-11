"use client";

import { useEffect, useRef, useState } from "react";
import { loadYouyunSettings, saveYouyunSettings, testYouyunConnection, type YouyunSettings } from "../lib/personal-youyun-api";

export function SystemSettings() {
  const [settings, setSettings] = useState<YouyunSettings | null>(null);
  const [publicKey, setPublicKey] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [region, setRegion] = useState("cn-wlcb");
  const [zone, setZone] = useState("cn-wlcb-01");
  const [busy, setBusy] = useState<"load" | "save" | "test" | null>("load");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);

  function begin(action: "load" | "save" | "test") {
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    setBusy(action); setError(""); setMessage("");
    return next;
  }
  async function load() {
    const next = begin("load");
    try {
      const value = await loadYouyunSettings(next.signal);
      if (next.signal.aborted) return;
      setSettings(value); setRegion(value.region); setZone(value.zone);
    } catch (err) { if (!next.signal.aborted) setError(err instanceof Error ? err.message : "读取配置失败。"); }
    finally { if (!next.signal.aborted) setBusy(null); }
  }
  useEffect(() => { void load(); return () => controller.current?.abort(); }, []);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const next = begin("save");
    try {
      const value = await saveYouyunSettings({ publicKey, privateKey, region, zone }, next.signal);
      if (next.signal.aborted) return;
      setSettings(value); setRegion(value.region); setZone(value.zone); setPublicKey(""); setPrivateKey("");
      setMessage("个人优云配置已保存，新建训练任务和推理服务将使用此账号。");
    } catch (err) { if (!next.signal.aborted) setError(err instanceof Error ? err.message : "保存失败。"); }
    finally { if (!next.signal.aborted) setBusy(null); }
  }
  async function test() {
    const next = begin("test");
    try {
      const value = await testYouyunConnection(next.signal);
      if (!next.signal.aborted) setMessage(value.message);
    } catch (err) { if (!next.signal.aborted) setError(err instanceof Error ? err.message : "连接失败。"); }
    finally { if (!next.signal.aborted) setBusy(null); }
  }

  return <section className="personal-settings">
    <div className="title-row"><div><h1>系统配置</h1><p className="subtitle">管理当前登录账号的优云智算连接</p></div></div>
    <form className="panel personal-settings-panel" onSubmit={save}>
      <div className="personal-settings-heading"><h2>我的优云智算</h2><span className={settings?.configured ? "personal-settings-ready" : "personal-settings-pending"}>{busy === "load" ? "正在读取…" : settings?.configured ? "已配置" : "待配置"}</span></div>
      <p>训练和推理使用你自己的优云账号及余额。训练数据集、模型和训练产物仍与平台用户共享。</p>
      {error && <div className="personal-settings-error" role="alert">{error}{!settings && <button type="button" className="secondary-btn" onClick={() => void load()} disabled={!!busy}>重新加载</button>}</div>}
      {message && <div className="personal-settings-success" role="status">{message}</div>}
      <fieldset disabled={!!busy || !settings}>
        <label>Public Key<input type="password" autoComplete="off" spellCheck={false} value={publicKey} onChange={event => setPublicKey(event.target.value)} placeholder={settings?.configured ? `已保存 ${settings.publicKeyMasked}，留空保持不变` : "输入优云 API Public Key"} required={!settings?.configured} maxLength={4096} /></label>
        <label>Private Key<input type="password" autoComplete="new-password" spellCheck={false} value={privateKey} onChange={event => setPrivateKey(event.target.value)} placeholder={settings?.configured ? "已加密保存，留空保持不变" : "输入优云 API Private Key"} required={!settings?.configured} maxLength={4096} /></label>
        <div className="personal-settings-grid">
          <label>默认地域<input value={region} onChange={event => setRegion(event.target.value)} placeholder="cn-wlcb" required maxLength={64} /></label>
          <label>默认可用区<input value={zone} onChange={event => setZone(event.target.value)} placeholder="cn-wlcb-01" required maxLength={80} /></label>
        </div>
        <p className="field-note">在优云控制台的 API 密钥管理中获取 Public Key / Private Key。示例：华北二区 cn-wlcb / cn-wlcb-01，上海二区 cn-sh2 / cn-sh2-02。</p>
      </fieldset>
      <div className="personal-settings-actions"><button type="submit" className="primary-btn" disabled={!!busy || !settings}>{busy === "save" ? "保存中…" : "保存配置"}</button><button type="button" className="secondary-btn" disabled={!!busy || !settings?.configured || !!publicKey || !!privateKey || region !== settings.region || zone !== settings.zone} onClick={() => void test()}>{busy === "test" ? "连接中…" : "测试已保存的连接"}</button></div>
      <p className="field-note">密钥加密保存在服务器，仅用于你的优云操作。修改配置只影响新建任务和服务；已有任务保留创建时的配置，以完成运行和资源释放。连接测试不会创建计费实例。</p>
    </form>
  </section>;
}
