"""Pure formatting and selection logic shared by the UI and diagnostics."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


def format_tokens(value:int)->str:
    value=max(0,value)
    for threshold,suffix in ((10**9,'B'),(10**6,'M'),(10**3,'K')):
        if value>=threshold:
            scaled=value/threshold
            precision=2 if scaled<100 else 1
            return f'{scaled:.{precision}f}{suffix}'
    return str(value)


def format_money(amount:Decimal,currency:str)->str:
    symbol={'USD':'$','CNY':'¥'}.get(currency,currency+' ')
    if amount==0:return symbol+'0.00'
    if 0<amount<Decimal('0.0001'):return '<'+symbol+'0.0001'
    quantum=Decimal('0.01') if amount>=1 else Decimal('0.0001')
    rounded=amount.quantize(quantum,rounding=ROUND_HALF_UP)
    return symbol+f'{rounded:,.{2 if amount>=1 else 4}f}'


def select_session(snapshot,pinned_id=None):
    if pinned_id:
        return next((s for s in snapshot.sessions if s.id==pinned_id),None)
    return next((s for s in snapshot.sessions if not s.is_subagent),None)


@dataclass(frozen=True)
class CostSummary:
    amounts:dict[str,Decimal]
    unpriced_events:int
    reasons:tuple[str,...]
    sources:tuple[str,...]

    @property
    def display(self):
        if not self.amounts:return '未定价'
        text=' + '.join(format_money(v,k) for k,v in sorted(self.amounts.items()))
        return ('≥' if self.unpriced_events else '')+text

    @property
    def label(self):
        if self.unpriced_events:return '部分估算' if self.amounts else '费用待配置'
        if len(self.sources)>1:return '混合单价估算'
        return self.sources[0] if self.sources else 'API参考估算'


def summarize_cost(events,book):
    amounts={};unknown=0;reasons=set();sources=set();seen=False
    for event in events:
        seen=True
        quote=book.quote(event.model,event.usage,event.service_tier,precise=event.precise)
        if quote.amount is None:
            if event.usage.get('total_tokens',event.usage.get('input_tokens',0)+event.usage.get('output_tokens',0)):
                unknown+=1
                reasons.add(quote.reason or f'{event.model}: 缺少单价或逐次明细')
        else:
            amounts[quote.currency]=amounts.get(quote.currency,Decimal(0))+quote.amount
            sources.add(quote.source)
            if quote.reason:reasons.add(quote.reason)
    if not seen:amounts={'USD':Decimal(0)}
    return CostSummary(amounts,unknown,tuple(sorted(reasons)),tuple(sorted(sources)))


def session_choices(snapshot,pinned_id=None,limit=100):
    """Recent main sessions, plus the current pin even if old or unavailable."""
    choices={'自动跟随最近活跃主会话':None}
    sessions=list(snapshot.sessions) if snapshot else []
    visible=[s for s in sessions if not s.is_subagent][:limit]
    if pinned_id and all(s.id!=pinned_id for s in visible):
        pinned=next((s for s in sessions if s.id==pinned_id),None)
        if pinned:visible.append(pinned)
        else:choices[f'锁定会话（日志暂不可用） · {pinned_id}']=pinned_id
    for session in visible:
        date=session.created_at.astimezone().strftime('%m-%d %H:%M') if session.created_at else '--'
        label=f'{date} · {session.id} · {session.model}'
        choices[label]=session.id
    return choices
