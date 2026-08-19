# 路线二:Avatar 参数木偶(身体动作 · 桌面模式原生)

无头显 FBT 路线已判死(见 DESIGN.md §12),身体动作走这条:给 Avatar 一次性加一层
"提线木偶"动画机,bot 经 OSC 拨参数就能摆弄躯干和手臂。**桌面模式无条件可用**,
不碰 SteamVR,不用校准,改一次永久生效。

## 原理(三句话)

1. Avatar 里每个"语义轴"是一个同步 float 参数(-1~+1),动画机用 blend tree
   在"两个极限姿势"之间按参数值插值——参数=提线,姿势=木偶的极限位。
2. bot 往 `/avatar/parameters/Puppet/*` 发 OSC,就是隔空拉线。控制台 `osc` 命令
   可以手动拨(见第 6 步),不用等 bot 后端。
3. **VRC Animator Tracking Control** 负责"把手臂/躯干从游戏 IK 手里借来
   (Animation)/还回去(Tracking)"——舞蹈 emote 同款机制,官方支持。

## v0 轴表(先 6+1 验证管线,好看再扩)

| 参数名(一字不差) | 类型 | 含义 | -1 端 | +1 端 | 默认值 |
|---|---|---|---|---|---|
| `Puppet/On` | Bool | 木偶总开关 | — | — | false |
| `Puppet/LeanX` | Float | 躯干左右倾 | 左倾 | 右倾 | 0 |
| `Puppet/LeanZ` | Float | 躯干前后倾 | 后仰 | 前倾 | 0 |
| `Puppet/ArmL_Up` | Float | 左臂抬起 | 自然垂下 | 举过头顶 | **-1** |
| `Puppet/ArmR_Up` | Float | 右臂抬起 | 自然垂下 | 举过头顶 | **-1** |
| `Puppet/ArmL_Fwd` | Float | 左臂前伸 | 微微后摆 | 前平举 | 0 |
| `Puppet/ArmR_Fwd` | Float | 右臂前伸 | 微微后摆 | 前平举 | 0 |

注意 `ArmUp` 的刻度:**0 = T 姿平举**(muscle 0 的天然含义),自然垂手在
**-1**——所以这两个参数默认值要填 -1,躯干轴 0 就是直立不用管。

同步成本:6×8 + 1 = **49 bits**(Avatar 总预算 256 bits)。
v1 再加:`Twist`(转体)、`ElbowL/R`(屈肘)、`HandCurlL/R`(握拳)、
`Bounce`(蹲弹)→ 满配 12 float + 1 bool = 97 bits。

## 第 0 步:动工前检查(把结果发我)

- 打开上传 Cahill 用的 Unity 工程(VCC / VRChat SDK3 Avatars)。
- 选中场景里的 Avatar → Inspector 找 **VRC Avatar Descriptor** →
  Expressions 区域 → 点开 **Parameters** 指着的那个资产。
- 面板底部有 **"Total Memory: xx/256"**——把这个数发我(v0 要占 49)。
- 顺带看一眼 Playable Layers 区域:**Action** 槽是空(Default)还是已经放了
  自定义控制器。空的最好办;有自定义的也能做,先告诉我。
- **动手前把整个工程目录复制一份当备份。**

## 第 1 步:声明参数(两处都要加,名字一字不差)

参数要在两个地方各写一遍,VRChat 运行时按名字桥接:

**1a. Expression Parameters 资产**(网络同步声明):
在第 0 步那个资产的 Inspector 里点 Add,逐行填上表 7 个参数——
类型照表(Bool/Float),**Default 照表最后一列**(`ArmL_Up`/`ArmR_Up`
填 **-1**,其余 0,`On` 填 false),**Saved = 关,Synced = 开**。

**1b. 动画控制器**(第 2 步建的那个):
打开控制器(双击)→ 左上 **Parameters** 页签 → "+" → 同名同类型加 7 个。
再额外加一个内部参数:**`One`,Float,默认值 1**——它不进 Expression
Parameters(不占同步位),只给第 4 步的 Direct 树当常量权重用。

## 第 2 步:自己的 Action 控制器

1. Project 窗口搜索 `vrc_AvatarV3ActionLayer`(SDK 自带),选中按 **Ctrl+D**
   复制,改名 `Cahill_Action`,挪到你自己的文件夹。
2. 拖进 Avatar Descriptor → Playable Layers → **Action** 槽。

为什么用 Action 层:它是官方给"全身接管"(舞蹈 emote/AFK)预留的层,
自带我们要抄的模板结构;FX 层动不了骨骼,Gesture 层要跟手势系统抢地盘。

## 第 3 步:生成极限姿势(12 个 .anim,脚本一键出)

**不要用 Animation 窗口录制**——人形(Humanoid)骨架由 Animator 的
muscle 系统接管,录制模式里在 Scene 手转骨骼会被直接弹回去,这是 Unity
的已知限制,不是操作问题。姿势的正确存法是 **muscle 曲线**(属性名形如
`Left Arm Down-Up`,跨 Avatar 通用可混合),但手拨要面对近百个滑条,
所以这步交给脚本:

1. 把仓库里的 **`unity/Editor/PuppetAnimGen.cs`** 复制进 Unity 工程的
   `Assets/Editor/` 文件夹(没有就新建一个,名字必须叫 `Editor`)。
2. 等右下角编译转完圈,菜单栏出现 **Tools → Anima → 生成木偶姿势片段**,
   点一下。
3. `Assets/Anima/PuppetAnims/` 下自动出现 12 个 clip:
   `Pose_LeanX_L/R`、`Pose_LeanZ_B/F`、`Pose_ArmL_Up_Min/Max`、
   `Pose_ArmR_Up_Min/Max`、`Pose_ArmL_Fwd_Min/Max`、`Pose_ArmR_Fwd_Min/Max`。
   若 Console 出红字"muscle 名不存在",把那行原样发我。
4. 预览:Project 里点选任意一个 clip,**Inspector 底部的预览窗格**会用
   默认小人摆出该姿势(把 Cahill 的模型拖进预览窗格可换成本人)。

每个 clip 都是 1 帧静止姿势,只含自己那 2~3 条 muscle 曲线,不碰 Hips
位移——不同轴天生不打架,没有"手滑多录了骨头"这回事。

幅度/方向要调:改脚本顶部 `Poses` 表里的数值(-1~1),再点一次菜单——
**原地覆盖、GUID 不变**,第 4 步 blend tree 里挂好的引用不会丢。

## 第 4 步:搭 blend tree(木偶的合成器)

1. 打开 `Cahill_Action` → **Layers** 页签 → "+" 新建层 `Puppet` →
   点该层右边齿轮,**Weight 拉到 1**。
2. 在这层空白处右键 → Create State > **Empty**,起名 `Idle`(默认态,啥也不放)。
3. 再右键 → Create State > **From New Blend Tree**,起名 `Puppet`,双击进入。
4. 选中根 Blend Tree,Inspector 里 **Blend Type = Direct**。
5. 点 "+" > **New Blend Tree**(不是 Add Motion Field)6 次——每个子树对应一轴。
   每个子项右边的权重参数都选 **`One`**(常量 1,六个子树同时全量生效)。
6. 双击进每个子树:**Blend Type = 1D**,Parameter 选对应轴(如 `Puppet/LeanX`),
   Add Motion Field 两次,**取消勾选 Automate Thresholds**,手填阈值:
   **-1 放 `*_L` / `*_B` / `*_Min`,+1 放 `*_R` / `*_F` / `*_Max`**。
7. 回到 `Puppet` 层:`Idle → Puppet` 拉 transition,条件 `Puppet/On` **true**;
   `Puppet → Idle` 条件 **false**。两条都:**Has Exit Time 关**,
   Settings 里 **Transition Duration 改 0.25**(淡入淡出)。

原理:Direct 树把 6 个 1D 子树的 muscle 曲线叠加——因为第 3 步保证了
每轴只碰自己的骨头,叠加不冲突;参数 0 时每轴都落在两端中点=自然位。

## 第 5 步:借身体 / 还身体(两个 State Behaviour)

选中 `Puppet` 状态 → Inspector 最下 **Add Behaviour**,加两个:

1. **VRC Playable Layer Control**:Layer = **Action**,Goal Weight = **1**,
   Blend Duration = 0.25。(Action 层平时权重是 0,不拉起来动画不生效——
   emote 模板同款。)
2. **VRC Animator Tracking Control**:先用 **A 档**——
   **Left Hand、Right Hand → Animation**,其余全部 **No Change**。

选中 `Idle` 状态 → 同样加两个,反着设:Layer Control Goal = **0**;
Tracking Control 把 **Left Hand、Right Hand → Tracking**(还给 IK)。

进游戏后如果 A 档下 LeanX/LeanZ 不明显(躯干被 IK 拽着),回来把两个
Tracking Control 各补一项 **Hip → Animation / Tracking**(**B 档**),
重传对比。两档差异告诉我。

## 第 6 步:上传 + 验证(不用 bot 大脑,控制台就行)

1. SDK 面板 Build & Publish,重传 Avatar,进游戏换上。
2. bot 侧只要 Anima 在跑(游戏和她在同一台机器),控制台敲:

   ```
   osc Puppet/On true
   osc Puppet/ArmL_Up 0.8     ← 左臂应该抬起来
   osc Puppet/ArmL_Up -1
   osc Puppet/LeanX 0.6       ← 躯干右倾(A 档不动就等 B 档)
   osc Puppet/On false        ← 应在 0.25 秒内回到正常站姿
   ```

3. **逐轴校方向**(muscle 的正负号是按 Unity 命名规则推的,个别轴可能反):

   - `osc Puppet/LeanX 1` → 应**右**倾
   - `osc Puppet/LeanZ 1` → 应**前**倾
   - `osc Puppet/ArmL_Up 1` → 左臂**举过头**(-1 应垂下,0 是 T 姿平举)
   - `osc Puppet/ArmL_Fwd 1` → 左臂**前平举**(右臂两轴同理)

   哪根轴反了就回脚本,把 `Poses` 表里**那一对 clip 的数值全部取负**,
   重点一次菜单、重传——引用不丢。把反了哪几根也发我,我改进仓库里的默认值。

4. 已知的 v0 观感限制:同步 float 是 8-bit 量化且远端不插值,**别人看**
   连续动的轴会有轻微台阶感(你自己那台的画面=本地视角,是丝滑的)。
   v1 加"远端平滑层"(VRCFT 生态的标准套路)解决,到时候我给你出。

## 验证结果发我之后,我接着做

- bot 侧参数木偶后端:现有 `PuppetDriver` 架构换个"地址后端"(trackers →
  Puppet/* 参数),sway/呼吸/情绪摆动等程序化生成器和 `puppet` 控制台命令
  全套迁过来,再把 `motion` 工具位接给大脑。
- v1 扩轴 + 远端平滑层的 Unity 增补步骤。

## 排错速查

| 症状 | 多半是 |
|---|---|
| 参数拨了完全没反应 | 1a/1b 两处名字不一致;或 Action 槽没挂 `Cahill_Action` |
| 姿势瞬间跳变没有过渡 | transition 忘了改 Duration 0.25 / Has Exit Time 没关 |
| 菜单点完 Console 红字"muscle 名不存在" | Unity 版本差异,红字原样发我 |
| 某轴方向反了 | 脚本 `Poses` 里那对数值全部取负,重跑菜单重传(见第 6 步) |
| 一轴动、另一轴跟着歪 | 自己改 `Poses` 时两轴写了同一条 muscle(默认表不会) |
| 手臂抬一半就被拽回去 | Tracking Control 没设 Animation,IK 还攥着手 |
| `Puppet/On true` 后角色僵直 T 姿 | 某个 1D 子树没放 clip 或阈值没改成 -1/1 |
