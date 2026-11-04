"""Project workflow routing, independent of terminal output and network I/O."""


def action_arguments(config, request, original):
    action = request.action
    if action != 'recovery-restore' and (getattr(request, 'id', None) or getattr(request, 'output', None)):
        raise ValueError('--id and --output require recovery-restore')
    if action != 'resolve' and (request.path is not None or request.choose is not None):
        raise ValueError('--path and --choose require --action resolve')
    if request.json and action not in {'plan', 'status', 'doctor', 'recovery-list', 'recovery-verify'}:
        raise ValueError('--json is not supported for this action')
    if action == 'start':
        return original
    if action in {'once', 'plan', 'resolve', 'doctor'} and config.command != 'sync':
        raise ValueError('This action requires a sync configuration')
    def options(*names):
        result=[]
        for name in names:
            value=getattr(config,name,None)
            if value is True:
                result.append('--'+name.replace('_','-'))
            elif value is not None and value is not False:
                result.append('--'+name.replace('_','-')+'='+str(value))
        return result
    if action in {'status','recovery-list','recovery-verify','recovery-restore'}:
        if not getattr(config,'state_dir',None):
            raise ValueError('Set state_dir explicitly for this action')
        if action == 'recovery-restore':
            if not request.id or request.output is None:
                raise ValueError('recovery-restore requires --id and --output')
            return ['recovery','restore',*options('state_dir'),'--output='+str(request.output),'--',request.id]
        result=['status'] if action == 'status' else ['recovery',action.split('-')[1]]
        return result+options('state_dir')+(['--json'] if request.json else [])
    credentials=options('token_file','ca_file','allow_http')
    if action == 'doctor':
        return ['doctor',*credentials,*(['--json'] if request.json else []),'--',config.url]
    if action == 'resolve':
        if not request.path or not request.choose:
            raise ValueError('resolve requires --path and --choose')
        return ['resolve',*credentials,*options('state_dir'),'--choose='+request.choose,'--',config.url,str(config.root),request.path]
    return ['sync',*credentials,*options('state_dir'),*(['--dry-run'] if action=='plan' else []),*(['--json'] if request.json else []),'--',config.url,str(config.root)]


def prepare_action(config, request):
    """Return operation parameters directly, without a CLI parsing round trip."""
    from types import SimpleNamespace
    validate_request(config, request)
    action=request.action
    values=dict(vars(config))
    if action=='start':
        return SimpleNamespace(**values)
    values['log_file']=None
    if action in {'once','plan'}:
        values.update(command='sync',watch=False,dry_run=action=='plan',json=request.json)
    elif action=='doctor':
        values.update(command='doctor',json=request.json)
    elif action=='status':
        values.update(command='status',json=request.json)
    elif action=='resolve':
        values.update(command='resolve',path=request.path,choose=request.choose)
    elif action.startswith('recovery-'):
        values.update(command='recovery',recovery_command=action.split('-')[1],json=request.json)
        if action=='recovery-restore':
            values.update(id=request.id,output=request.output)
    return SimpleNamespace(**values)


def validate_request(config, request):
    action = request.action
    allowed = {'start','once','plan','status','doctor','resolve','recovery-list','recovery-verify','recovery-restore'}
    if action not in allowed:
        raise ValueError('Unknown project action')
    if action != 'recovery-restore' and (getattr(request,'id',None) or getattr(request,'output',None)):
        raise ValueError('--id and --output require recovery-restore')
    if action != 'resolve' and (request.path is not None or request.choose is not None):
        raise ValueError('--path and --choose require resolve')
    if request.json and action not in {'plan','status','doctor','recovery-list','recovery-verify'}:
        raise ValueError('--json is not supported for this action')
    if action in {'once','plan','resolve','doctor'} and config.command != 'sync':
        raise ValueError('This action requires a sync configuration')
    if (action=='status' or action.startswith('recovery-')) and not getattr(config,'state_dir',None):
        raise ValueError('Set state_dir explicitly for this action')
    if action=='resolve' and (not request.path or request.choose not in {'local','remote'}):
        raise ValueError('resolve requires --path and --choose local/remote')
    if action=='recovery-restore' and (not request.id or request.output is None):
        raise ValueError('recovery-restore requires --id and --output')
