"""Machine-readable public speech contract; same defaults as request validation."""
from backend.speech_contracts import DEFAULTS


def specification() -> dict:
    properties = {
        'text':{'type':'string','minLength':1,'maxLength':300,'description':'目标文本；可使用括号描述语气'},
        'requestId':{'type':'string','pattern':'^[A-Za-z0-9_-]{1,80}$','description':'幂等键。相同键与参数返回原任务；不同参数返回409'},
        'mode':{'type':'string','enum':['design','clone','continue','combined']},
        'referenceAudioId':{'type':'string','nullable':True,'description':'clone/combined 必填，上传接口返回的 aud- ID'},
        'promptAudioId':{'type':'string','nullable':True,'description':'continue/combined 必填'},
        'promptText':{'type':'string','nullable':True,'maxLength':300,'description':'与提示音频逐字一致；与 promptAudioId 配对'},
    }
    for key, low, high in [('steps',4,20),('cfg',1,4),('seed',0,2147483647),('minTokens',1,1024),('maxTokens',16,2048),('retryMaxTimes',0,3),('retryRatio',1,20)]:
        properties[key]={'type':'number' if key in ('cfg','retryRatio') else 'integer','minimum':low,'maximum':high}
    for key in ('normalize','retryBadcase','streaming'):
        properties[key]={'type':'boolean'}
    for key, default in DEFAULTS.items():
        properties[key]['default']=default
    job_schema={'type':'object','additionalProperties':False,'required':['text'],'properties':properties}
    definitions=[
        ('/service','get','查询模型状态与能力'),('/service/start','post','异步加载并预热'),('/service/stop','post','停止模型并取消所有待处理任务'),
        ('/jobs','post','创建任务；忙碌时409'),('/batches','post','提交1–5个排队任务'),('/jobs','get','分页、搜索和筛选任务'),
        ('/jobs/{jobId}','get','查询任务详情'),('/jobs/{jobId}','delete','删除终态任务及产物'),
        ('/jobs/{jobId}/cancel','post','取消任务；运行中取消会重新加载模型'),('/jobs/{jobId}/retry','post','用原参数创建新任务'),
        ('/jobs/{jobId}/audio','get','下载完整48kHz PCM16 WAV'),('/jobs/{jobId}/chunks/{index}','get','读取流式WAV片段；index从0开始'),
        ('/assets','post','上传0.5–30秒音频，最大10MB；返回素材ID'),('/assets','get','列出音频素材'),
        ('/assets/{assetId}/audio','get','预览24kHz单声道参考音频'),('/assets/{assetId}','delete','删除未被任务引用的素材'),
    ]
    paths={}
    for path,method,title in definitions:
        binary=path.endswith('/audio') or '/chunks/' in path
        status='202' if method=='post' and path in ('/jobs','/batches') else '201' if method=='post' and path=='/assets' else '200'
        operation={'summary':title,'tags':['VoxCPM2'],'responses':{status:{'description':title,'content':{'audio/wav':{'schema':{'type':'string','format':'binary'}}} if binary else {'application/json':{'schema':{'$ref':'#/components/schemas/Envelope'}}}},'400':{'description':'参数或音频无效'},'401':{'description':'需要登录'},'404':{'description':'资源不存在'},'409':{'description':'状态冲突、队列已满或素材被引用'},'413':{'description':'上传超过10MB'},'503':{'description':'服务未部署或不可用'}}}
        params=[]
        for name in ('jobId','assetId','index'):
            if '{'+name+'}' in path:
                params.append({'name':name,'in':'path','required':True,'schema':{'type':'integer','minimum':0} if name=='index' else {'type':'string'}})
        if path in ('/jobs','/assets') and method=='get':
            params += [{'name':'page','in':'query','schema':{'type':'integer','minimum':1,'default':1}}, {'name':'pageSize','in':'query','schema':{'type':'integer','minimum':1,'maximum':50 if path=='/jobs' else 100,'default':20}}]
            if path=='/jobs': params += [{'name':key,'in':'query','schema':{'type':'string'}} for key in ('query','state')]
        if method=='post' and path in ('/jobs','/batches'):
            schema={'$ref':'#/components/schemas/GenerateRequest'} if path=='/jobs' else {'type':'object','required':['items'],'additionalProperties':False,'properties':{'items':{'type':'array','minItems':1,'maxItems':5,'items':{'$ref':'#/components/schemas/GenerateRequest'}}}}
            operation['requestBody']={'required':True,'content':{'application/json':{'schema':schema}}}
        if method=='post' and path=='/assets':
            params.append({'name':'name','in':'query','schema':{'type':'string','maxLength':120}})
            operation['requestBody']={'required':True,'content':{'application/octet-stream':{'schema':{'type':'string','format':'binary'}}}}
        if params: operation['parameters']=params
        paths.setdefault(path,{})[method]=operation
    envelope = {
        'type':'object', 'required':['requestId','traceId','data','error','meta'],
        'properties': {
            'requestId':{'type':'string'}, 'traceId':{'type':'string'},
            'data':{'type':'object','nullable':True,'additionalProperties':True},
            'error':{'type':'object','nullable':True,'properties':{
                'code':{'type':'string'},'message':{'type':'string'},'retryable':{'type':'boolean'}}},
            'meta':{'type':'object','properties':{
                'timestamp':{'type':'string','format':'date-time'},'apiVersion':{'type':'string'}}},
        },
    }
    return {
        'openapi':'3.0.3',
        'info':{'title':'VoxCPM2 CPU Speech API','version':'2.0',
            'description':'Authenticate through the XShipAI desktop: POST /api/login, GET /api/session for csrf, POST /api/launch/cppt. Use the session cookie for speech API calls. Never expose the private worker key.'},
        'servers':[{'url':'/api/speech'}], 'security':[{'DesktopSession':[]}], 'paths':paths,
        'components':{
            'securitySchemes':{'DesktopSession':{'type':'apiKey','in':'cookie','name':'xshipai_session'}},
            'schemas':{'GenerateRequest':job_schema,'Envelope':envelope},
        },
    }
