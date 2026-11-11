"use client";

import { useEffect, useRef, useState } from "react";
import { speechApi, speechAudio, speechChunk, speechDefaults, modeLabels, stateLabels, requestId, SpeechRequest, SpeechJob, SpeechMode } from "../lib/speech-api";
import { useSpeechWorkbench } from "../lib/use-speech-workbench";
import { SpeechApiDocs, copyText, saveFile } from "./speech-api-docs";
import s from "./speech-module.module.css";

type Tab = "details" | "use" | "history" | "assets" | "api";
const tabs: Array<[Tab,string]> = [["details","模型详情"],["use","在线使用"],["history","任务记录"],["assets","音频素材"],["api","接口文档"]];
const stamp = (value?: string|null) => value ? new Date(value).toLocaleString("zh-CN",{hour12:false}) : "暂无数据";
const busy = (job: SpeechJob) => ["queued","running"].includes(job.state);
function payloadFrom(job: SpeechJob): SpeechRequest {
  return { ...speechDefaults, ...Object.fromEntries(Object.keys(speechDefaults).map(key => [key,job[key as keyof SpeechRequest]])) };
}

function Waveform({ id }: { id: string }) {
  const [peaks,setPeaks] = useState<number[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    const context = new AudioContext();
    void fetch(speechAudio(id),{credentials:"include",signal:controller.signal})
      .then(r => { if (!r.ok) throw new Error(); return r.arrayBuffer(); })
      .then(data => context.decodeAudioData(data)).then(audio => {
        if (controller.signal.aborted) return;
        const data = audio.getChannelData(0), result: number[] = [];
        const stride = Math.max(1,Math.floor(data.length/100));
        for (let i=0;i<100;i++) {
          let peak=0;
          for (let j=i*stride;j<Math.min(data.length,(i+1)*stride);j+=8) peak=Math.max(peak,Math.abs(data[j]));
          result.push(peak);
        }
        setPeaks(result);
      }).catch(() => {}).finally(() => { void context.close(); });
    return () => controller.abort();
  }, [id]);
  return <svg className={s.waveform} viewBox="0 0 600 70" role="img" aria-label="生成音频的波形"><line x1="0" y1="35" x2="600" y2="35" stroke="#d8e4f5" />{peaks.map((peak,i) => <rect key={i} x={i*6} y={35-Math.max(2,peak*34)} width="3" height={Math.max(4,peak*68)} rx="1" fill="#3575ce" />)}</svg>;
}

function StreamPlayer({ job }: { job: SpeechJob }) {
  const [playing,setPlaying] = useState(false);
  const [message,setMessage] = useState("");
  const latest = useRef(job); latest.current = job;
  const context = useRef<AudioContext|null>(null);
  useEffect(() => {
    if (!playing) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>, index=0, at=0;
    const tick = async () => {
      try {
        while (!controller.signal.aborted && index < latest.current.chunkCount) {
          const response = await fetch(speechChunk(job.id,index),{credentials:"include",signal:controller.signal});
          if (!response.ok) throw new Error("流式片段暂时不可用");
          const data = await response.arrayBuffer();
          const ctx = context.current;
          if (!ctx || controller.signal.aborted) return;
          const buffer = await ctx.decodeAudioData(data);
          if (controller.signal.aborted) return;
          const source = ctx.createBufferSource(); source.buffer=buffer; source.connect(ctx.destination);
          at=Math.max(at,ctx.currentTime+.08); source.start(at); at+=buffer.duration; index++;
          setMessage(`已读取 ${index} 个片段`);
        }
        if (!controller.signal.aborted && (busy(latest.current) || index<latest.current.chunkCount)) timer=setTimeout(tick,750);
      } catch (e) { if (!controller.signal.aborted) { setMessage((e as Error).message); setPlaying(false); } }
    };
    void tick();
    return () => { controller.abort(); clearTimeout(timer); void context.current?.close(); context.current=null; };
  }, [playing,job.id]);
  return <div className={s.stream}><button onClick={() => {
    if (playing) { setPlaying(false); return; }
    context.current=new AudioContext(); void context.current.resume(); setPlaying(true);
  }}>{playing ? "停止流式试听" : "开启流式试听"}</button><span>{message || `已生成 ${job.chunkCount} 个片段，${job.generatedSeconds || 0} 秒`}</span><small>CPU 生成速度低于实时速度时，片段之间会有等待。</small></div>;
}

function Result({ job }: { job: SpeechJob }) {
  const audio = useRef<HTMLAudioElement>(null);
  return <div className={s.result}>
    <div className={s.row}><span className={s.badge} data-state={job.state}>{stateLabels[job.state] || job.state}</span><span>{modeLabels[job.mode]}</span></div>
    <blockquote>{job.text}</blockquote>
    {job.streaming && <StreamPlayer key={job.id} job={job} />}
    {job.state === "completed" ? <>
      <Waveform id={job.id} /><audio ref={audio} key={job.id} controls preload="metadata" src={speechAudio(job.id)} aria-label="生成音频" />
      <div className={s.row}><label>试听倍速 <select defaultValue="1" onChange={e => { if (audio.current) audio.current.playbackRate=Number(e.target.value); }}>{[.75,1,1.25,1.5,2].map(n => <option key={n} value={n}>{n}×</option>)}</select></label><a href={speechAudio(job.id)} download={`voxcpm2-${job.id}.wav`}>下载 WAV</a></div>
      <dl className={s.metrics}><div><dt>音频时长</dt><dd>{job.durationSeconds} s</dd></div><div><dt>生成耗时</dt><dd>{job.generationSeconds} s</dd></div><div><dt>实时率 RTF</dt><dd>{job.generationSeconds && job.durationSeconds ? (job.generationSeconds/job.durationSeconds).toFixed(2) : "暂无数据"}</dd></div><div><dt>采样率</dt><dd>{(job.sampleRate || 48000)/1000} kHz</dd></div></dl>
    </> : <p className={s.hint}>{job.message || (job.state === "queued" ? "已进入服务器队列，关闭页面不影响执行。" : "CPU 正在生成。任务进度和音频片段会自动更新。")}</p>}
    {job.failureCode && <p className={s.error}>{job.failureCode}：{job.message}</p>}
    <details><summary>请求参数与产物信息</summary><pre className={s.code}>{JSON.stringify(job,null,2)}</pre></details>
  </div>;
}

export function SpeechModule({ management = false }: { management?: boolean }) {
  const model = useSpeechWorkbench();
  const {service,jobs,assets,pending,error,connectionError,checkedAt,run,refresh,setError} = model;
  const [detail,setDetail] = useState(false), [tab,setTab] = useState<Tab>("details");
  const [form,setForm] = useState<SpeechRequest>({...speechDefaults});
  const [selection,setSelection] = useState<string|null>(null);
  const [search,setSearch] = useState(""), [stateFilter,setStateFilter] = useState("");
  const [modelSearch,setModelSearch] = useState("");
  const [batch,setBatch] = useState(false), [batchText,setBatchText] = useState("你好，欢迎使用语音合成。\n今天的天气真不错。");
  const [compare,setCompare] = useState<string[]>([]);
  const [notice,setNotice] = useState("");
  const [recording,setRecording] = useState(false), [canRecord,setCanRecord] = useState(false);
  const recorder = useRef<MediaRecorder|null>(null);
  const recordTimer = useRef<ReturnType<typeof setTimeout>>();
  const mounted = useRef(true);
  useEffect(() => {
    const available=new Set(jobs.filter(job=>job.state==="completed").map(job=>job.id));
    setCompare(old=>old.some(id=>!available.has(id))?old.filter(id=>available.has(id)):old);
  }, [jobs]);
  useEffect(() => {
    mounted.current=true;
    setCanRecord(window.isSecureContext && Boolean(navigator.mediaDevices?.getUserMedia));
    if (window.location.pathname === "/speech") {
      const target = new URLSearchParams(window.location.search).get("tab");
      if (tabs.some(([key]) => key===target)) { setTab(target as Tab); setDetail(true); }
    }
    return () => { mounted.current=false; clearTimeout(recordTimer.current); recorder.current?.stream.getTracks().forEach(track => track.stop()); if (recorder.current?.state === "recording") recorder.current.stop(); };
  }, []);
  function open(next: Tab) { setDetail(true); setTab(next); setNotice(""); }
  function patch<K extends keyof SpeechRequest>(key: K, value: SpeechRequest[K]) { setForm(old => ({...old,[key]:value})); }
  function mode(next: SpeechMode) {
    setForm(old => ({...old,mode:next,referenceAudioId:["clone","combined"].includes(next)?old.referenceAudioId:null,promptAudioId:["continue","combined"].includes(next)?old.promptAudioId:null,promptText:["continue","combined"].includes(next)?old.promptText:null}));
  }
  const ready=service?.state==="ready" && !connectionError;
  const current=jobs.find(j=>j.id===selection) || jobs[0];
  const reference=["clone","combined"].includes(form.mode), prompt=["continue","combined"].includes(form.mode);
  const visible=jobs.filter(j=>(!stateFilter||j.state===stateFilter)&&j.text.toLowerCase().includes(search.toLowerCase()));
  async function upload(file: Blob,name: string,target?: "referenceAudioId"|"promptAudioId") {
    if (!file.size) { setError("音频文件为空，请选择有效音频"); return; }
    if (file.size>10*1024*1024) { setError("音频不能超过 10 MB"); return; }
    await run(async signal => {
      const item=await speechApi.upload(file,name,signal);
      if (!signal.aborted) {
        if (target) setForm(old => {
          const applicable=target==="referenceAudioId"?["clone","combined"].includes(old.mode):["continue","combined"].includes(old.mode);
          return applicable?{...old,[target]:item.id}:old;
        });
        setNotice(`已上传 ${item.name}（${item.durationSeconds} 秒）`);
      }
    });
  }
  async function startRecording() {
    try {
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});
      if (!mounted.current) { stream.getTracks().forEach(track=>track.stop()); return; }
      const next=new MediaRecorder(stream), pieces: BlobPart[]=[];
      recorder.current=next;
      next.ondataavailable=event=>{ if(event.data.size) pieces.push(event.data); };
      next.onstop=()=>{ clearTimeout(recordTimer.current); stream.getTracks().forEach(track=>track.stop()); if (!mounted.current) return; setRecording(false); void upload(new Blob(pieces,{type:next.mimeType}),`录音-${new Date().toLocaleTimeString("zh-CN")}.webm`); };
      next.start(); setRecording(true); recordTimer.current=setTimeout(()=>{if(next.state==="recording")next.stop();},29000);
    } catch { setError("无法使用麦克风，请检查浏览器权限或上传音频文件"); }
  }
  async function submit() {
    const texts=batch?batchText.split("\n").map(t=>t.trim()).filter(Boolean):[form.text.trim()];
    if (!texts.length||texts.length>5||texts.some(t=>t.length>300)) { setError("每批 1–5 条，每条文本 1–300 字"); return; }
    await run(async signal => {
      const created=await speechApi.batch(texts.map(text=>({...form,text,requestId:requestId()})),signal);
      if(!signal.aborted){setSelection(created[0].id);setNotice(`已提交 ${created.length} 个任务，离开页面后仍会继续生成。`);}
    });
  }
  const banner=<>{(error||connectionError)&&<div className={s.error} role="alert">{error||connectionError}<button onClick={()=>{setError("");void refresh().catch(e=>setError(e.message));}}>重试</button>{connectionError&&<small>下面显示的是最近一次成功读取的数据；暂时不能提交任务。</small>}</div>}{notice&&<div className={s.notice} role="status">{notice}</div>}</>;
  const uploadInput=(target?: "referenceAudioId"|"promptAudioId")=><label className={s.fileButton}>上传音频<input type="file" accept="audio/*,.wav,.mp3,.flac,.m4a,.ogg,.webm" disabled={pending} onChange={e=>{const file=e.target.files?.[0];if(file)void upload(file,file.name,target);e.target.value="";}} /></label>;

  if (!detail) return <section className={s.root} aria-label="语音模型列表">
    <header className={s.header}><div><h1>{management?"语音合成模型":"语音合成推理服务"}</h1><p>管理已部署的语音模型，进入详情后进行语音测试、任务管理和 API 调用。</p></div></header>
    {banner}<div className={s.overview}><div><span>已部署模型</span><strong>{service?1:"—"}</strong></div><div><span>可用模型</span><strong>{service?service.state==="ready"?1:0:"—"}</strong></div><div><span>待处理任务</span><strong>{service?(service.activeJobId?1:0)+(service.queuedJobs||0):"—"}</strong></div><div><span>运行环境</span><strong>本机 CPU</strong></div></div>
    <div className={s.toolbar}><input aria-label="搜索语音模型" placeholder="搜索模型名称或仓库" value={modelSearch} onChange={e=>setModelSearch(e.target.value)} /><button onClick={()=>void refresh().catch(e=>setError(e.message))}>刷新</button></div>
    <div className={s.tableWrap}><table><thead><tr><th>模型名称</th><th>模型来源</th><th>状态</th><th>运行环境</th><th>能力</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{service&&("voxcpm2 openbmb/voxcpm2".includes(modelSearch.toLowerCase()))?<tr><td><button className={s.textButton} onClick={()=>open("details")}><b>VoxCPM2</b></button></td><td>OpenBMB/VoxCPM2</td><td><span className={s.badge} data-state={service.state}>{stateLabels[service.state]}</span></td><td>CPU · {service.threads} 线程</td><td>声音设计 / 克隆 / 续写 / 流式</td><td>{stamp(service.updatedAt)}</td><td><div className={s.actions}>{([["details","详情"],["use","使用"],["api","接口文档"]] as Array<[Tab,string]>).map(([key,label])=><button key={key} className={s.textButton} onClick={()=>open(key)}>{label}</button>)}</div></td></tr>:<tr><td colSpan={7} className={s.empty}>{service?"没有匹配的语音模型":connectionError?"无法读取模型列表，请重试":"正在读取已部署模型…"}</td></tr>}</tbody></table><footer>数据来自部署服务 · 最近刷新 {stamp(checkedAt)}</footer></div>
  </section>;

  return <section className={s.root} aria-label="VoxCPM2 模型工作台">
    <button className={s.back} onClick={()=>{setDetail(false);setNotice("");}}>← 返回语音模型列表</button>
    <header className={s.header}><div><h1>VoxCPM2 <span className={s.badge} data-state={service?.state}>{service?stateLabels[service.state]:"读取中"}</span></h1><p>OpenBMB/VoxCPM2 · CPU 语音模型 · 48 kHz 输出</p></div><div className={s.actions}><button disabled={pending||!service||["ready","loading","stopping"].includes(service.state)} onClick={()=>void run(async signal=>{await speechApi.lifecycle("start",signal);})}>启动模型</button><button disabled={pending||!service||service.state==="stopped"} onClick={()=>void run(async signal=>{await speechApi.lifecycle("stop",signal);})}>停止模型{service?.activeJobId||service?.queuedJobs?"并取消队列":""}</button></div></header>
    <nav className={s.tabs} aria-label="模型功能">{tabs.map(([key,label])=><button key={key} aria-current={tab===key?"page":undefined} className={tab===key?s.active:""} onClick={()=>setTab(key)}>{label}</button>)}</nav>
    {banner}{service?.state==="loading"&&<div className={s.notice} role="status">正在加载模型并执行真实合成预热，完成后自动变为可用。</div>}{service?.state==="failed"&&<div className={s.error}>模型运行失败：{service.failureCode}。请重新启动后重试。</div>}
    {tab==="details"&&<div className={s.panel}><div className={s.row}><h2>模型详情</h2><a href="https://www.modelscope.cn/models/OpenBMB/VoxCPM2" target="_blank" rel="noreferrer">查看官方模型</a></div><dl className={s.details}><dt>模型仓库</dt><dd>OpenBMB/VoxCPM2</dd><dt>运行版本</dt><dd>VoxCPM {service?.runtimeVersion||"读取中"}</dd><dt>部署设备</dt><dd>CPU · {service?.threads||"—"} 线程 · 12 GiB 容器内存上限</dd><dt>文件校验</dt><dd>{service?.modelVerified?"官方文件大小及 SHA256 校验通过":"尚未确认"}</dd><dt>服务标识</dt><dd>{service?.id||"—"}</dd><dt>最新状态</dt><dd>{stamp(service?.updatedAt)}</dd><dt>支持模式</dt><dd>声音设计、音色克隆、音频续写、克隆与续写组合</dd><dt>输出能力</dt><dd>完整 WAV、流式 WAV 片段、批量排队</dd><dt>资源限制</dt><dd>单任务执行，最多等待 5 个任务；每条 300 字，运行限时 20 分钟</dd><dt>参考素材</dt><dd>0.5–30 秒、最大 10 MB；建议 3–15 秒清晰单人声音</dd></dl><h3>选择合适的生成模式</h3><div className={s.modeDescriptions}>{Object.entries(modeLabels).map(([key,label])=><div key={key}><h4>{label}</h4><p>{key==="design"?"直接输入文本，可在括号内描述声音、情绪或语气。":key==="clone"?"上传参考音频提取音色，无需填写参考音频文本。":key==="continue"?"上传提示音频并填写其对应文本，延续说话方式与上下文。":"参考音频控制音色，提示音频与文本提供上下文，两者可独立选择。"}</p><button className={s.textButton} onClick={()=>{mode(key as SpeechMode);setTab("use");}}>使用此模式</button></div>)}</div><p className={s.hint}>CPU 速度取决于文本长度与参数。可先用短句、4 步测试。当前未加载独立降噪模型或 LoRA Adapter；页面不会将这些能力显示为可用。</p></div>}
    {tab==="use"&&<div className={s.workbench}>
      <form className={s.panel} onSubmit={e=>{e.preventDefault();void submit();}}>
        <h2>生成设置</h2><div className={s.modes}>{(Object.entries(modeLabels) as Array<[SpeechMode,string]>).map(([key,label])=><button key={key} type="button" className={form.mode===key?s.active:""} onClick={()=>mode(key)}>{label}</button>)}</div>
        {(reference||prompt)&&<fieldset><legend>参考音频</legend>{([...(reference?["referenceAudioId"]:[]),...(prompt?["promptAudioId"]:[])] as Array<"referenceAudioId"|"promptAudioId">).map(key=><div key={key} className={s.audioField}><label htmlFor={key}>{key==="referenceAudioId"?"音色参考":"续写提示音频"}</label><div className={s.row}><select id={key} required value={form[key]||""} onChange={e=>patch(key,e.target.value||null)}><option value="">选择已上传素材</option>{assets.map(item=><option key={item.id} value={item.id}>{item.name} · {item.durationSeconds}s</option>)}</select>{uploadInput(key)}</div>{form[key]&&<audio controls src={speechAudio(form[key]!,"assets")} preload="metadata" />}</div>)}{prompt&&<label>提示音频对应文本<textarea required maxLength={300} rows={2} value={form.promptText||""} onChange={e=>patch("promptText",e.target.value)} placeholder="逐字填写提示音频中的内容" /></label>}<p className={s.hint}>支持 WAV、MP3、FLAC、M4A、OGG、WebM；0.5–30 秒、最大 10 MB。</p></fieldset>}
        <div className={s.row}><label htmlFor="speech-text">{batch?"批量文本（每行一条）":"目标文本"}</label><label className={s.check}><input type="checkbox" checked={batch} onChange={e=>setBatch(e.target.checked)} />批量测试</label></div>
        <textarea id="speech-text" required rows={5} maxLength={batch?1504:300} value={batch?batchText:form.text} onChange={e=>batch?setBatchText(e.target.value):patch("text",e.target.value)} />
        <div className={s.row}><div className={s.presets}>{["(温柔地) 你好，今天过得怎么样？","(新闻播报) 欢迎收听今天的新闻。","你好，welcome to XShipAI。"].map((text,i)=><button type="button" key={text} onClick={()=>{setBatch(false);patch("text",text);}}>{["温柔语气","新闻播报","中英混合"][i]}</button>)}</div><small>{batch?`${batchText.split("\n").filter(t=>t.trim()).length} / 5 条`:`${form.text.length} / 300 字`}</small></div>
        <fieldset><legend>生成参数</legend><div className={s.parameters}>{([
          ["steps","推理步数",4,20,1,"更多步数通常更慢"],["cfg","引导强度 CFG",1,4,.1,"控制文本遵循程度"],["seed","随机种子",0,2147483647,1,"相同参数便于对比"],
          ["minTokens","最小生成长度",1,1024,1,"模型长度单位，非秒"],["maxTokens","最大生成长度",16,2048,1,"达到上限可能截断音频"],
          ["retryMaxTimes","异常重试次数",0,3,1,"仅非流式重试生效"],["retryRatio","异常长度比例阈值",1,20,.1,"音频与文本的比例判断"],
        ] as Array<[keyof SpeechRequest,string,number,number,number,string]>).map(([key,label,min,max,step,hint])=><label key={key}>{label}<input type="number" required min={min} max={max} step={step} value={Number(form[key])} onChange={e=>patch(key,Number(e.target.value))} disabled={key.startsWith("retry")&&(!form.retryBadcase||form.streaming)} /><small>{hint}</small></label>)}</div><div className={s.checks}><label><input type="checkbox" checked={form.normalize} onChange={e=>patch("normalize",e.target.checked)} />文本规范化（数字、日期）</label><label><input type="checkbox" checked={form.streaming} onChange={e=>patch("streaming",e.target.checked)} />流式输出</label><label><input type="checkbox" checked={form.retryBadcase} disabled={form.streaming} onChange={e=>patch("retryBadcase",e.target.checked)} />异常长度自动重试</label></div><div className={s.actions}><button type="button" onClick={()=>patch("seed",Math.floor(Math.random()*2147483647))}>随机种子</button><button type="button" onClick={()=>setForm({...speechDefaults,mode:form.mode,text:form.text,referenceAudioId:form.referenceAudioId,promptAudioId:form.promptAudioId,promptText:form.promptText})}>重置参数</button></div></fieldset>
        <button className={s.primary} type="submit" disabled={pending||!ready||(reference&&!form.referenceAudioId)||(prompt&&(!form.promptAudioId||!form.promptText?.trim()))}>{pending?"正在提交…":!ready?"请先启动并等待模型可用":batch?"提交批量任务":service?.activeJobId?"加入生成队列":"生成语音"}</button><p className={s.hint}>任务保存在服务器上，离开页面也会继续。取消运行中任务需要重新加载模型。</p>
        <details><summary>请求 JSON / 参数导出</summary><pre className={s.code}>{JSON.stringify(form,null,2)}</pre><div className={s.actions}><button type="button" onClick={()=>saveFile("voxcpm2-request.json",JSON.stringify(form,null,2))}>导出参数</button><button type="button" onClick={()=>void copyText(JSON.stringify(form,null,2)).then(()=>setNotice("已复制请求参数")).catch(e=>setError(e.message))}>复制 JSON</button><button type="button" onClick={()=>setTab("api")}>查看调用代码</button></div></details>
      </form>
      <aside className={s.panel}><div className={s.row}><h2>生成结果</h2><span>{service?.queuedJobs||0} 个等待中</span></div>{current?<><Result key={current.id} job={current} /><div className={s.actions}>{busy(current)?<button disabled={pending} onClick={()=>void run(async signal=>{await speechApi.action(current.id,"cancel",signal);})}>取消此任务</button>:<button disabled={pending||!ready} onClick={()=>void run(async signal=>{const next=await speechApi.action(current.id,"retry",signal);if(!signal.aborted)setSelection(next.id);})}>重新生成</button>}<button onClick={()=>{setForm(payloadFrom(current));setNotice("已回填任务参数");}}>回填参数</button><button onClick={()=>saveFile(`${current.id}.json`,JSON.stringify(current,null,2))}>导出记录</button></div></>:<div className={s.empty}>尚无生成记录。选择模式并提交第一条测试。</div>}<h3>最近任务</h3><div className={s.recent}>{jobs.slice(0,8).map(job=><button key={job.id} onClick={()=>setSelection(job.id)} className={current?.id===job.id?s.selected:""}><span>{job.text}</span><small>{stateLabels[job.state]}</small></button>)}</div></aside>
    </div>}
    {tab==="history"&&<div className={s.panel}><div className={s.row}><h2>任务记录</h2><button onClick={()=>saveFile("voxcpm2-history.json",JSON.stringify(visible,null,2))}>导出筛选结果</button></div><div className={s.toolbar}><input placeholder="搜索文本" aria-label="搜索任务文本" value={search} onChange={e=>setSearch(e.target.value)} /><select aria-label="任务状态" value={stateFilter} onChange={e=>setStateFilter(e.target.value)}><option value="">全部状态</option>{["queued","running","completed","failed","cancelled"].map(state=><option key={state} value={state}>{stateLabels[state]}</option>)}</select><span>共 {visible.length} 条 · 最近 50 条</span></div><div className={s.tableWrap}><table><thead><tr><th>对比</th><th>文本 / 时间</th><th>模式</th><th>参数</th><th>状态</th><th>耗时 / 时长</th><th>操作</th></tr></thead><tbody>{visible.map(job=><tr key={job.id}><td><input aria-label={`对比 ${job.id}`} type="checkbox" disabled={job.state!=="completed"||(!compare.includes(job.id)&&compare.length>=2)} checked={compare.includes(job.id)} onChange={e=>setCompare(e.target.checked?[...compare,job.id]:compare.filter(id=>id!==job.id))} /></td><td className={s.textCell}>{job.text}<small>{stamp(job.createdAt)}</small></td><td>{modeLabels[job.mode]}</td><td>{job.steps}步 / CFG {job.cfg}<small>seed {job.seed}</small></td><td><span className={s.badge} data-state={job.state}>{stateLabels[job.state]}</span></td><td>{job.generationSeconds??"—"}s / {job.durationSeconds??"—"}s</td><td><div className={s.actions}><button className={s.textButton} onClick={()=>{setSelection(job.id);setTab("use");}}>查看</button><button className={s.textButton} onClick={()=>{setForm(payloadFrom(job));setTab("use");}}>复用</button>{busy(job)?<button disabled={pending} className={s.textButton} onClick={()=>void run(async signal=>{await speechApi.action(job.id,"cancel",signal);})}>取消</button>:<><button className={s.textButton} disabled={pending||!ready} onClick={()=>void run(async signal=>{await speechApi.action(job.id,"retry",signal);})}>重试</button><button className={s.textButton} disabled={pending} onClick={()=>{if(window.confirm("删除这条任务及其音频？此操作无法撤销。"))void run(async signal=>{await speechApi.deleteJob(job.id,signal);if(!signal.aborted)setCompare(old=>old.filter(id=>id!==job.id));});}}>删除</button></>}</div></td></tr>)}{!visible.length&&<tr><td colSpan={7} className={s.empty}>没有匹配的任务</td></tr>}</tbody></table></div><div className={s.row}><h3>结果对比（最多两条）</h3><button disabled={!compare.length} onClick={()=>setCompare([])}>清空对比</button></div><p className={s.hint}>勾选已完成任务，可同时试听并比较参数、耗时和音频长度。</p><div className={s.comparison}>{compare.map(id=>jobs.find(j=>j.id===id)).filter((j):j is SpeechJob=>Boolean(j)).map(job=><div className={s.panel} key={job.id}><p>{job.steps} 步 · CFG {job.cfg} · seed {job.seed}</p><Result job={job} /></div>)}</div></div>}
    {tab==="assets"&&<div className={s.panel}><div className={s.row}><div><h2>音频素材</h2><p>供音色克隆和续写使用。最多 100 个素材；被任务引用的素材需先移除相关任务才能删除。</p></div><div className={s.actions}>{uploadInput()}<button disabled={pending||!canRecord} onClick={()=>recording?recorder.current?.stop():void startRecording()}>{recording?"停止并保存录音":"麦克风录音"}</button></div></div>{!canRecord&&<p className={s.hint}>麦克风录音需要 HTTPS 或 localhost。当前地址可通过上传文件使用全部参考音频模式。</p>}{recording&&<p role="status">正在录音，最长 29 秒…</p>}<div className={s.tableWrap}><table><thead><tr><th>素材名称</th><th>时长 / 规格</th><th>试听</th><th>上传时间</th><th>操作</th></tr></thead><tbody>{assets.map(item=><tr key={item.id}><td>{item.name}<small>{item.id}</small></td><td>{item.durationSeconds} s · {item.sampleRate/1000} kHz<small>{(item.audioBytes/1024).toFixed(1)} KB</small></td><td><audio controls preload="none" src={speechAudio(item.id,"assets")} /></td><td>{stamp(item.createdAt)}</td><td><div className={s.actions}><button className={s.textButton} onClick={()=>{mode("clone");patch("referenceAudioId",item.id);setTab("use");}}>用于克隆</button><button className={s.textButton} onClick={()=>{mode("continue");patch("promptAudioId",item.id);setTab("use");}}>用于续写</button><button className={s.textButton} disabled={pending} onClick={()=>{if(window.confirm("删除此音频素材？"))void run(async signal=>{await speechApi.deleteAsset(item.id,signal);});}}>删除</button></div></td></tr>)}{!assets.length&&<tr><td colSpan={5} className={s.empty}>上传一段参考音频，开始音色克隆或续写。</td></tr>}</tbody></table></div></div>}
    {tab==="api"&&<div className={s.panel}><SpeechApiDocs request={form} /></div>}
    <footer className={s.footer}>状态来自实际部署服务 · 最近刷新 {stamp(checkedAt)}</footer>
  </section>;
}
