"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { speechApi, SpeechAsset, SpeechJob, SpeechService } from "./speech-api";

export function useSpeechWorkbench() {
  const [service,setService] = useState<SpeechService|null>(null);
  const [jobs,setJobs] = useState<SpeechJob[]>([]);
  const [assets,setAssets] = useState<SpeechAsset[]>([]);
  const [connectionError,setConnectionError] = useState("");
  const [error,setError] = useState("");
  const [pending,setPending] = useState(false);
  const [checkedAt,setCheckedAt] = useState<string|null>(null);
  const controller = useRef<AbortController>();
  const revision = useRef(0);
  const refresh = useCallback(async () => {
    const signal = controller.current?.signal;
    if (!signal || signal.aborted) return;
    const own = ++revision.current;
    const [nextService,nextJobs,nextAssets] = await Promise.all([speechApi.service(signal),speechApi.jobs(signal),speechApi.assets(signal)]);
    if (!signal.aborted && own === revision.current) {
      setService(nextService); setJobs(nextJobs); setAssets(nextAssets);
      setConnectionError(""); setCheckedAt(new Date().toISOString());
    }
  }, []);
  useEffect(() => {
    const current = new AbortController(); controller.current = current;
    let timer: ReturnType<typeof setTimeout>, failures = 0;
    const poll = async () => {
      try { await refresh(); failures = 0; }
      catch (e) { if (!current.signal.aborted) { failures++; setConnectionError((e as Error).message); } }
      if (!current.signal.aborted) timer = setTimeout(poll,Math.min(30000,3000 * (failures + 1)));
    };
    void poll();
    return () => { current.abort(); clearTimeout(timer); };
  }, [refresh]);
  async function run(action: (signal: AbortSignal) => Promise<void>) {
    const signal = controller.current?.signal;
    if (!signal || signal.aborted || pending) return;
    setPending(true); setError("");
    try { await action(signal); await refresh(); }
    catch (e) { if (!signal.aborted) setError((e as Error).message); }
    finally { if (!signal.aborted) setPending(false); }
  }
  return { service,jobs,assets,pending,error,connectionError,checkedAt,run,refresh,setError };
}
