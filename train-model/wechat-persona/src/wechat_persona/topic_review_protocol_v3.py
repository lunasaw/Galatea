"""Clarified semantic rubric; retain the frozen v2 schema and eligibility rules."""
from .topic_review_protocol import METHOD as AXES_CONTRACT, SYSTEM as PREVIOUS_SYSTEM


METHOD = 'topic-reply-axes-v3'
# No fixture answers or recommended confidence numbers belong in the prompt.
SYSTEM = PREVIOUS_SYSTEM + '''
以下是各轴的操作定义。对每条按关联、所指、沟通行为、风险分别核验，不能让一轴代替另一轴。
1. 回应关联只判断这句话接的是什么话，不判断它是否值得学习。拒绝、反驳、威胁和不安全建议也可能
明确回应 self；此时保留真实关联类型和 self 锚点，另在风险轴标风险。回答更早的一个问题是有效关联，
不用回答前文的全部问题，也不默认回应最近一句。确定不了具体 self 锚点才用 undetermined。
2. 上下文完整性要求能确定回复中选择、承诺、评价、答应或拒绝的具体对象/行动/事件。
只有“答应/同意”的动作清楚还不够：如果前文只指向未提供的线下谈话或未说明的安排，
具体承诺什么仍未知，应为 missing_external 或 missing_referent，即使 relation 是 acknowledgement。
如果前文已经说清行动、对象和必要条件，则不需要知道那次谈话的全部经过。
回复新增明确的信息不等于缺上下文；不要求验证陈述是否为真。可识别的第三方称呼不需要真实身份档案。
必要信息只在图片/语音等未提供媒体中时用 missing_media。目标回复不能替代缺失的前文所指。
3. communicative_value 衡量是否完成清楚的沟通行为，不是信息量、措辞稀有度、训练收益或回复长度。
接受/拒绝邀请、致谢、回礼式问候、确认修正、明确选择、安慰和有依据的照顾均属 useful，
即使回复很短、常见、只确认对方所说的话。不要因没有新增事实而标 low_signal。
low_signal 只用于可以确定没有可学习沟通行为的噪声或无意义填充；无法确定则 undetermined。
缺必要对象的简短答应可以有沟通行为而上下文不充分，两轴分别填写。
4. 风险针对目标回复实际做了什么，不因前文出现敏感话题就自动给安全的拒绝/保护隐私建议标风险。
普通第三方提及不等于敏感泄露；泄露未获许可的健康、隐私内容、控制胁迫、危险建议或不实能力声明
应独立标 flagged。风险已明确不应让关系轴变成 undetermined。
5. confidence 是对以上分轴结论及原消息证据判断的确定程度，包括确定的风险或缺失判断；
不是回复优质程度、事实真实性、语气强弱或主观上愿不愿意选入训练的分数。
如仍有实际歧义，应如实降低置信度或使用 undetermined。不得为了通过任何门槛虚报置信度。
6. 输出前检查结构一致性：四类 linked relation 必须给非空 self 回应锚点；
self_continuation/unrelated/undetermined 的 responds_to_indices 必须为空；
required_context_indices 可保留用于判断缺失或自身续话的历史证据，且包含全部确定回应锚点。
'''
