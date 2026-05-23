# Home Assistant 完整设置指南 — HomeBrain v2.0 设备底座

> **目标主机**: `192.168.66.68`
> **预期耗时**: 15-20 分钟（含初始化等待）
> **你的角色**: 你需要在 HA Web UI 中手动完成账户创建、插件添加、设备导入和 Token 生成
> **前置条件**: HA Docker 容器已通过 `deploy-ha.sh` 部署成功

---

## 概览

```
Step 1 → 创建 HA 管理员账户（Web UI 首次访问）
Step 2 → 安装 xiaomi_home 插件（运行脚本）
Step 3 → 在 HA 中添加 Xiaomi Home 集成
Step 4 → 小米 OAuth 登录 & 导入设备
Step 5 → 创建 Long-Lived Access Token（供 HomeBrain 使用）
Step 6 → 验证设备导入 & 本地控制状态
```

---

## Step 1: 创建 HA 管理员账户

**目标**: 访问 HA Web UI，完成首次启动向导，创建你的管理员账户。

### 操作步骤

1. **打开浏览器**，访问: **http://192.168.66.68:8123**

2. **等待初始化** — 首次访问可能需要 1-3 分钟初始化。你会看到一个加载页面或 "Preparing Home Assistant" 的提示。

   > **预期结果**: 页面加载完成后，出现 "Create your account" 表单。

3. **填写账户信息**:
   - **Name**: 你的名字（如 `Sir`）
   - **Username**: 登录用户名（如 `sir` 或 `admin`）
   - **Password**: 设置一个强密码（至少 8 位，含字母+数字）
   - **Confirm Password**: 再次输入密码

   ![预期界面] 表单包含 Name / Username / Password / Confirm Password 四个字段，下方有一个蓝色 CREATE ACCOUNT 按钮。

4. **点击 CREATE ACCOUNT**

5. **（可选）配置家庭位置**:
   - 系统会询问你的家庭位置，用于自动化时区/日出日落计算
   - 在地图上点击你的大致位置（如北京/上海），或输入 `Asia/Shanghai`，点击 NEXT

6. **（可选）数据共享**:
   - 系统会询问是否匿名分享使用数据给 HA 官方
   - 选择你喜欢的方式，点击 NEXT/FINISH

7. **进入主界面**:
   - 完成后你会看到 HA 的主仪表盘（Overview），初始可能为空（没有添加任何设备）

   > **预期结果**: 
   > - 左下角显示你的用户名
   > - 顶部有 Overview / Energy / Map / Logbook / History / Media / To-do lists / Developer Tools 等标签
   > - 中间区域显示 Welcome 信息或空仪表盘

---

## Step 2: 安装 xiaomi_home 插件

**目标**: 运行安装脚本，将小米官方 xiaomi_home 插件安装到 HA。

### 操作步骤

1. **SSH 登录到目标主机**:
   ```bash
   ssh root@192.168.66.68
   ```

2. **如果脚本在本地，先上传**:
   ```bash
   # 在本地机器上执行（如果你在 192.168.66.68 以外的机器上）
   scp install-xiaomi-hacs.sh root@192.168.66.68:/root/
   ```

3. **运行安装脚本**:
   ```bash
   bash /root/install-xiaomi-hacs.sh
   ```

   > **脚本自动完成**: 
   > - 检查 HA 容器运行状态
   > - 从 GitHub 克隆最新版 xiaomi_home 插件
   > - 安装到 `/opt/homeassistant/config/custom_components/xiaomi_home/`
   > - 自动重启 HA 容器
   > - 等待 HA 重新就绪

4. **等待脚本执行完成** — 通常 1-2 分钟。

   > **预期结果**: 
   > - 终端输出 `xiaomi_home 插件安装完成`
   > - 显示 `✓ manifest.json 已确认`
   > - 显示 `✓ __init__.py 已确认`
   > - 显示 `✓ config_flow.py 已确认`
   > - HA Web UI 重新可访问

5. **验证安装**:
   - 在 HA Web UI 中: **Settings → System → Logs**
   - 搜索 `xiaomi_home`，如果有类似 "Loaded xiaomi_home" 的日志，说明插件加载成功
   - 也可以在 **Settings → Devices & Services → INTEGRATIONS** 查看（Xiaomi Home 应该出现在可添加列表中）

---

## Step 3: 添加 Xiaomi Home 集成

**目标**: 在 HA 中添加 Xiaomi Home 集成，准备导入设备。

### 操作步骤

1. **进入集成页面**:
   - HA Web UI → 左下角 **Settings**（齿轮图标）
   - 点击 **Devices & Services**

   > **预期界面**: 页面顶部有 Integrations / Devices / Entities / Helpers / Areas / Labels / Categories 标签。默认在 Integrations 标签。

2. **添加新集成**:
   - 点击右下角的 **+ ADD INTEGRATION** 按钮（蓝色/橙色圆形加号）

   ![预期界面] 弹出一个搜索对话框，标题为 "Add integration"。

3. **搜索 Xiaomi Home**:
   - 在搜索框中输入 `Xiaomi Home`（或 `xiaomi`）
   - 从搜索结果中点击 **Xiaomi Home**

   > **预期结果**: 出现 Xiaomi Home 的配置界面，中间有一个大按钮。

---

## Step 4: 小米 OAuth 登录 & 导入设备

**目标**: 通过小米 OAuth 授权，将你米家 App 中的所有设备导入到 HA。

### 操作步骤

1. **点击登录按钮**:
   - 在 Xiaomi Home 配置界面，点击 **"Click here to login"** 按钮

   > **预期结果**: 弹出一个新的浏览器窗口/标签页，跳转到小米 OAuth 登录页面（`account.xiaomi.com`）。

2. **登录小米账号**:
   - 输入你的「小米账号」手机号/邮箱和密码
   - 如果需要验证码，输入收到的短信验证码

   ![预期界面] 小米登录页面，上方是小米 Logo，中间是手机号/邮箱输入框和密码输入框。

3. **授权 HA 访问**:
   - 登录成功后，会显示授权确认页面
   - 页面列出 HA 请求的权限（读取家庭信息、设备列表、设备状态等）
   - 点击 **授权** 或 **Agree / 同意**

4. **选择米家家庭**:
   - 如果账号下有多个家庭（如 "我的家"、"父母家"），会出现选择列表
   - 选择你想要导入设备的家庭

   > **预期结果**: 授权成功后，浏览器标签页自动关闭，HA Xiaomi Home 配置页面显示 "Success" 或设备导入进度。

5. **等待设备导入**:
   - HA 会自动扫描并导入该家庭下的所有小米设备
   - 这是一个自动过程，可能需要 30 秒到 2 分钟（取决于设备数量）

   > **预期结果**: 
   > - HA Xiaomi Home 集成卡片显示 "Xiaomi Home" 已配置
   > - 显示已导入的设备数量

6. **完成配置**:
   - 点击 **Finish** 或 **Submit** 退出配置向导

---

## Step 5: 创建 Long-Lived Access Token

**目标**: 创建一个长期有效的 API Token，供 HomeBrain 连接 HA 使用。

### 操作步骤

1. **进入 Profile 页面**:
   - 点击 HA 界面左下角的 **你的用户名**（圆形头像图标或用户名文本）

   > **预期界面**: 弹出 Profile 页面，顶部显示用户名和头像。

2. **进入 Security 设置**:
   - 向下滚动到页面最底部
   - 找到 **Security** 区域

3. **创建 Long-Lived Access Token**:
   - 点击 **Long-Lived Access Tokens** 下的 **CREATE TOKEN** 按钮

   > **预期界面**: 弹出一个对话框，包含 Name 输入框。

4. **命名 Token**:
   - 在 **Name** 输入框中输入: `HomeBrain`
   - 点击 **OK** 或 **CREATE**

   > **预期结果**: 弹出一个对话框显示生成的 Token（一长串字符）。

5. **⚠️ 立即复制 Token**:
   - **这个 Token 只显示一次！** 关闭对话框后无法再次查看。
   - 选中 Token 文本（可以双击或拖选），Ctrl+C 复制
   - 粘贴到一个安全的地方暂存（如记事本）

   > **Token 格式**: 类似 `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI...` 的长字符串

6. **关闭对话框** — 确认已复制 Token 后关闭。

### 保存 Token

运行 save-ha-token.sh 脚本:

```bash
bash /root/save-ha-token.sh
```

脚本会:
- 引导你粘贴 Token（输入不可见，保证安全）
- 将 Token 写入 `/opt/homeassistant/config/homebrain-token.txt`
- 设置文件权限为 600（仅 root 可读）
- 自动验证 Token 是否有效（调用 HA API 测试）
- 输出 HomeBrain .env 配置行

> **预期结果**: 终端提示 "Token 保存完成"，并打印出需要在 HomeBrain .env 中添加的配置行。

---

## Step 6: 验证设备导入 & 本地控制状态

**目标**: 确认所有小米设备已成功导入，并检查是否启用本地控制模式。

### 6A. 验证设备导入

1. **查看已导入设备**:
   - **Settings → Devices & Services**
   - 找到 **Xiaomi Home** 集成卡片
   - 卡片上显示的数字 = 已导入设备数量
   - 点击该卡片查看设备列表

2. **查看所有设备**:
   - **Settings → Devices & Services → Devices** 标签
   - 可以看到所有 HA 识别的设备（包括小米设备和其他设备）
   - 小米设备通常会带有原始中文名称

   > **预期结果**: 
   > - 设备列表长度与你在米家 App 中的 Wi-Fi/Zigbee 设备数量基本一致
   > - 蓝牙 mesh / 红外 / 虚拟设备不会出现（这是正常的、官方的排除列表）

3. **查看实体 (Entities)**:
   - **Settings → Devices & Services → Entities** 标签
   - 搜索 `xiaomi` 可过滤出所有小米相关实体
   - 每个实体有 entity_id（如 `light.ke_ting_deng`、`sensor.wen_shi_du_chuan_gan_qi_temperature`）

   > **预期结果**: 实体数量通常多于设备数量（一个设备可能有多个实体，如温湿度传感器有 temperature + humidity 两个实体）

### 6B. 验证本地控制模式

**前提**: 中枢网关 firmware ≥ 3.3.0_0023（如不确定，先运行 `bash check-gateway-fw.sh` 获取检查方法）

1. **检查设备控制模式**:
   - **Settings → Devices & Services**
   - 点击 **Xiaomi Home** 集成卡片
   - 查看设备列表中的某个设备 → 点击进入设备详情
   - 在设备信息中查找 **"Local"** 或 **"本地"** 标签

   > **预期结果（本地模式）**: 
   > - 设备卡片或详情显示 "Local" 或 "本地" 标志
   > - 控制延迟 < 50ms（几乎即时响应）

   > **如果不是本地控制**: 
   > - 设备可能显示为 Cloud 连接
   > - 控制延迟 200-500ms
   > - 确认中枢网关 firmware 版本，如低于 3.3.0_0023 需先升级

2. **验证中枢网关连接**:
   - 在 **Settings → Devices & Services → Xiaomi Home** 中
   - 找到中枢网关设备（通常命名为 "中枢网关" 或 "Xiaomi Central Gateway"）
   - 查看其连接状态是否为 "Connected" 或 "已连接"

### 6C. 功能测试（建议）

1. **测试设备控制**:
   - 在 **Overview** 仪表盘中，点击某个灯的开关按钮
   - 或者进入某个设备的详情页，调整开关/亮度/模式
   - 观察实际设备是否响应

2. **测试状态同步**:
   - 手动打开/关闭一个物理设备（如按墙壁开关）
   - 在 HA 中观察状态是否实时更新（1-3 秒内）

3. **（可选）使用 Developer Tools 测试 API**:
   - **Developer Tools → Services**
   - 选择一个 service（如 `light.turn_on`）
   - 在 Target 中选择 entity（如 `light.ke_ting_deng`）
   - 点击 CALL SERVICE
   - 观察设备是否响应

---

## 完成检查清单

在进入 HomeBrain 集成阶段前，请确认以下所有项目:

- [ ] **Step 1**: HA Web UI 可正常访问，已创建管理员账户
- [ ] **Step 2**: xiaomi_home 插件安装成功，HA 重启后可访问
- [ ] **Step 3**: Xiaomi Home 集成已添加到 HA
- [ ] **Step 4**: 小米 OAuth 登录成功，设备已导入（检查设备列表）
- [ ] **Step 5**: Long-Lived Access Token 已创建并保存（`/opt/homeassistant/config/homebrain-token.txt` 存在且权限为 600）
- [ ] **Step 6a**: 设备导入验证通过（设备数量与米家 App 对比合理）
- [ ] **Step 6b**: 本地控制模式验证（如 firmware 满足条件）
- [ ] **Step 6c**: 至少控制一个设备成功响应

---

## 常见问题 (FAQ)

### Q1: HA Web UI 一直显示 "Preparing Home Assistant" 怎么办？

**A**: 首次启动 HA 需要下载和初始化数据库，可能需要 3-5 分钟。
- SSH 到主机: `ssh root@192.168.66.68`
- 查看 HA 日志: `docker logs homeassistant -f`
- 查找关键日志: 是否有 ERROR、是否有 "Setup completed" 消息
- 如果长时间无响应，尝试重启: `docker restart homeassistant`

### Q2: xiaomi_home 插件安装成功但 HA 中搜索不到？

**A**: 
- 确认 HA 已重启（安装脚本会自动重启，但可能需要额外等待）
- SSH 查看日志: `docker logs homeassistant 2>&1 | grep -i xiaomi`
- 确认插件文件在正确位置: `ls /opt/homeassistant/config/custom_components/xiaomi_home/`
- 如果缺少 `manifest.json`，说明安装不完整，重新运行脚本

### Q3: 小米 OAuth 登录后设备列表为空？

**A**:
- 确认登录的米家账号与米家 App 中是同一个
- 确认选择正确的家庭（如果有多个家庭）
- 在 Xiaomi Home 集成中，点击「重新加载」(RELOAD) 或「配置」(CONFIGURE)
- 检查 HA 日志是否有错误: `docker logs homeassistant 2>&1 | grep -i xiaomi`

### Q4: 中枢网关 firmware 版本不满足本地控制要求？

**A**:
- 在米家 App 中检查中枢网关是否有固件更新
- 小米已全面推送 3.3.0+ 版本，大概率只需 OTA 升级即可
- 如果暂时无法升级 → 降级为 Cloud 模式，仍可正常使用，HomeBrain 无需改动

### Q5: Token 丢失了怎么办？

**A**: Token 无法找回，必须在 HA 中重新创建:
1. Profile → Security → Long-Lived Access Tokens
2. 先删除旧的 Token（如果还在列表中）
3. 点击 CREATE TOKEN 创建新的
4. 重新运行 `bash /root/save-ha-token.sh`

### Q6: 如何删除/重新配置 Xiaomi Home 集成？

**A**:
- Settings → Devices & Services → Xiaomi Home 卡片
- 点击右上角「...」菜单 → Delete
- 删除后，所有从该集成创建的设备和实体会被移除
- 重新添加: ADD INTEGRATION → 搜索 Xiaomi Home → 重新登录

---

## 相关文件

| 文件 | 用途 |
|---|---|
| `docker-compose.ha.yml` | HA Docker 部署配置 |
| `deploy-ha.sh` | HA 一键部署脚本 |
| `install-xiaomi-hacs.sh` | xiaomi_home 插件安装脚本 |
| `save-ha-token.sh` | HA API Token 安全保存脚本 |
| `check-gateway-fw.sh` | 中枢网关 firmware 检查工具 |
| `HA-SETUP-GUIDE.md` | 本文档 |

---

**下一步**: 完成上述所有步骤后，通知 Jarvis / CodeForge 进入 P1 阶段 — 开发 HomeBrain HA Bridge 模块。
