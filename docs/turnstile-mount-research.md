# Turnstile 组件挂载门控研究报告（2026-10-03/04）

## 研究问题

CF 注册页（dash.cloudflare.com/sign-up）的 Turnstile 组件（后端明确要求 visible 模式 + 真生产 key）为什么在我们的所有测试环境中都拒绝挂载？ClickSolver 的天花板到底在哪一层？

## 实验矩阵（全部单次尝试，无重试轰炸）

| # | 实验 | 环境变量 | 挂载结果 |
|---|---|---|---|
| 1 | patchright（stealth Chromium 补丁版） | 自动化+补丁 | ❌ 0 iframe / 无 token |
| 2 | 纯 playwright（未打补丁） | 自动化无补丁 | ❌ 同上，行为完全一致 |
| 3 | 干净 Chrome（子进程直启，页面加载期间零 CDP 客户端，加载后 attach 观察） | 无自动化（加载期） | ❌ 同上 |
| 4 | 干净 Chrome + `--no-proxy-server`（广州家宽直连，对照 JP 数据中心代理） | 出口 IP 换家宽 | ❌ 同上 |
| 5 | 拟人行为注入（随机贝塞尔鼠标游走 + 逐键输入 40-130ms 抖动 + hover 后点击） | 行为信号 | ❌ 同上，提交仍 "Please complete the CAPTCHA" |
| 6 | Firefox 155（playwright） | 引擎切换 | ❌ 表单本身未渲染（更早层拦截） |

## 网络证据（抓包）

```
GET /api/v4/captcha/challenge?context=signup
  → challenge_required: true, key: 0x4AAAAAAAJel0iaAR3mgkjp, widget_mode: "visible"
Turnstile 引擎加载并执行深握手（fo / ci / brunhild 遥测，14+ 请求；pat 认证 2×401）
组件挂载：从未发生（0 iframe、无 shadow DOM（含穿透扫描）、无 token 字段）
POST /api/v4/user/create → 400 {"code":1200,"message":"Please fill out a CAPTCHA"}
```

对照（挂载/通过环境）：官方测试 key 页面（本地、CF demo、2captcha demo）token 自动发放，且握手只有浅层 api.js、无 pat——深握手仅出现在真 key 挑战流。

## 结论

1. **门控不在客户端**。六个维度的排除（自动化框架、CDP 存在性、引擎补丁、出口 IP 信誉、拟人行为、浏览器引擎）产生完全一致的结果——不存在"再 stealth 一点就挂载"的客户端杠杆。
2. **这是服务端按会话的"软墙"**：后端返回 challenge_required=true（要求挑战）但**抑制组件渲染**——被标记的会话看到的是"请完成验证码"而页面上没有任何验证码。客户端无题可解，ClickSolver 无靶可打。
3. **可能的会话标记信号**（未逐一分离，供后续）：全新 profile 无 CF 域 cookie 史、zaraz 遥测聚合评分、临时邮箱域名在流程中被查询（`/sso/connector?domain=yzcalo.com` 虽 404 但暴露了域名）、ASN/地区组合。
4. **对"90% 环境"目标的修正认知**：Turnstile 防御的完整梯度是
   - 被动放行（无组件）→ 隐形自动通过（发 token）→ **服务端软墙（要求挑战但不渲染）** → 交互挑战（可点击，ClickSolver 的战场）→ 交互挑战+持续风控
   我们的栈实测稳定处于前两层；第三层无解于客户端；ClickSolver 只对第四层有效，而 CF 对高危会话直接跳到第三层。

## 后续唯一有效路径

- 人工基线：真实日常浏览器（带 cookie 历史）打开同一页，确认人类是否可见验证码——若可见，缺的是**会话信誉积累**（养 profile），不是技术
- 会话信誉工程：预热 profile（访问 CF 站点攒 cookie）、非一次性邮箱、稳定住宅 IP——全部是环境工程
- 该结论同样适用于 devin 类项目的风控对抗策略：优先级应为"避免进入软墙状态"，而非"解更难的题"
