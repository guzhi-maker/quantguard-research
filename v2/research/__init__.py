"""QuantGuard 量化研究 v2 核心包。

模块划分：
- preferences：风险预算（CVaR 主轴）与期限偏好解析
- panel：原始快照读取与可成交性掩码
- features：特征构建、中性化、去冗余、IC 衰减
- labels：标签生成与防泄漏纪律
- primary：分位数主模型
- meta：元模型仓位乘数
- portfolio：CVaR 约束组合构建
- diagnosis：持仓诊断
- advice：建议引擎
- validation：滚动验证与防过拟合审计
- evidence：证据包组装
- report：AI 报告生成与校验
- pipeline：端到端编排
"""

__all__ = [
    "preferences",
    "panel",
    "features",
    "labels",
    "primary",
    "meta",
    "portfolio",
    "diagnosis",
    "advice",
    "validation",
    "evidence",
    "report",
    "pipeline",
]
