# 机械CAD图纸智能审核原型（DXF / BOM / STEP）

这是一个可实际运行的**单用户、本地试验系统**。它不会修改原始设计文件，也不宣称通过全部机械设计审查。

## 2026年11月7日工程专家评审整改

已对评审指出的五项高优先级缺陷和多项检查范围问题开展修订，新增针对性回归用例。具体情况、可复算证据及尚不支持的功能见 [《工程专家评审整改及复验记录》](docs/机械CAD审核_专家评审整改与复验记录.md)。

当前Qwen／DeepSeek仅解释已有规则发现的疑点，尚不能独立读取CAD图形或理解完整的三维装配。后续视觉能力和CAD工具调用需要独立研发及验收。

## 已实现

- DXF：识别人工指定尺寸文字、零长度直线、非正半径圆、未声明单位、冲突图签属性；记录DXF图元句柄。
- BOM：读取UTF-8或GB18030 CSV；查找缺失/重复零件号、非法数量；与DXF图签中的明确零件号、材料字段比较。
- STEP：通过Open CASCADE XDE读取装配结构与名称；使用包围盒筛选及实体布尔交集识别静态重叠，报告关联零件和交集体积。
- Web：上传文件、查看审核项目、二维轮廓预览、点击问题定位、下载JSON审核报告。

**判定边界**：人工覆盖尺寸、未声明单位和STEP实体重叠均为待核查事项，不等于设计错误。当前不支持DWG、GD&T语义分析、完整尺寸链、实体间运动干涉或自动判定配合合理性。没有可靠单位时，不换算到mm。DXF图形预览是简化图，不完整呈现块、圆弧、字体和尺寸渲染；曲线多段线会显示为折线，不能据此判断实际几何。

## Qwen与DeepSeek双模型接口（本地自主选择）

规则引擎可完全离线使用。网页默认选择 **不调用模型**。只有明确选择Qwen或DeepSeek，才会向对应官方接口传送本次工程规则发现事项的摘要与相关证据；**不会传送DWG/DXF/STEP原文件及网页预览图**。目前模型负责解释已发现的规则疑点，尚未具有独立视觉读图、工具自动执行或复杂设计安全结论功能。

两套服务端配置独立：

| 服务商 | 密钥环境变量 | 模型环境变量 | API地址环境变量 | 默认模型 |
|---|---|---|---|---|
| Qwen（阿里云百炼） | QWEN_API_KEY | QWEN_MODEL | QWEN_BASE_URL | qwen-plus |
| DeepSeek | DEEPSEEK_API_KEY | DEEPSEEK_MODEL | DEEPSEEK_BASE_URL | deepseek-flash |

例如在Mac终端为当前会话设置（请使用自己实际的API Key）：

```bash
export QWEN_API_KEY='your-qwen-api-key'
export DEEPSEEK_API_KEY='your-deepseek-api-key'
export QWEN_MODEL='qwen-plus'
export DEEPSEEK_MODEL='deepseek-flash'

uvicorn cad_audit.api:app --host 127.0.0.1 --port 8000
```

Qwen默认地址为阿里云百炼北京地域的兼容接口；如果API Key属于其他地域或业务空间，请依据阿里云控制台填写其官方 `QWEN_BASE_URL`，并确保地址以 `/compatible-mode/v1` 结尾。DeepSeek默认地址是 `https://api.deepseek.com`。接口地址允许的域名范围受服务端校验限制，不能通过网页填写任意URL。

可通过 `GET /api/model-providers` 查看两家是否已配置（绝不返回密钥），通过 `POST /api/review` 表单的 `provider=off|qwen|deepseek` 选择。本地规则检查结果总是保留在返回的 `results`、`findings` 中。模型分析位于 `ai_review` 字段；当密钥缺失、无待解释的疑点或者模型失败时，分别报告对应状态。

测试（不需要真实API Key，也不会产生云端调用费用）：

```bash
python -m pip install -e . pytest
python -m pytest -q tests/test_models.py
```

**目前没有获得用户密钥，尚未运行两家真实付费API请求。** 双接口的请求构造、服务商切换、规则证据筛选、异常处理和密钥不泄漏行为采用模拟HTTPS响应测试。生产使用前需要在实际开通的服务地域，用真实API Key进行一次端到端验证。


## 在Mac上安全配置模型密钥

请先到Qwen和DeepSeek官方控制台撤销已在聊天中发送的旧密钥，再生成两把新密钥。不要把新密钥发到聊天窗口、GitHub、源码或终端命令参数中。

先在本机项目目录 `cad-audit-mvp` 安装依赖，运行交互式配置工具：

```bash
python -m pip install -e .
python scripts/configure_api_keys.py
python scripts/check_model_connection.py
```

配置工具使用隐藏式终端交互，创建本机 `.env` 文件（权限600），Git 已忽略该文件。服务启动时自动读取 `.env`；已有环境变量优先。`check_model_connection.py` 会分别使用已配置的官方API执行一个模拟工程疑点的真实请求，因此会产生少量API费用。不会显示密钥。

Qwen的密钥需要与实际地域和计费计划对应；北京公共服务默认使用 `https://dashscope.aliyuncs.com/compatible-mode/v1`。跨地域工作空间、Token Plan或Coding Plan请按阿里云控制台说明修改 `.env` 中的 `QWEN_BASE_URL`。目前服务器只允许已登记的官方HTTPS地址。DeepSeek默认使用 `https://api.deepseek.com`。

以上是**本地部署配置**，不能通过GitHub仓库自动设置正在使用的Mac上的环境变量。没有用户重新生成的新密钥，无法完成两家收费API的正式连通性验证。

## 快速启动

建议Python 3.11—3.13，安装所需依赖（首次安装Open CASCADE包可能较大）：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn cad_audit.api:app --host 127.0.0.1 --port 8000
```

浏览器打开：`http://127.0.0.1:8000`。

自动测试：

```bash
pip install pytest httpx cadquery
pytest -q
```

## CSV格式

```csv
part_no,material,qty
P-001,SUS304,2
```

也支持`图号,材料,数量`等列名。与DXF比较时，图纸需通过图框INSERT属性提供`PART_NO`和`MATERIAL`。不从文件名猜测零件编号。

## 后续研发优先顺序

1. CAD原生接口 / DWG：保留尺寸关联、图块及外部参照。
2. 二维图纸与STEP特征关联：孔系、安装接口及孔轴配合检查。
3. 工程标准规则库：以企业正式规范及已核实标准数据表为依据。
4. 稳定性与安全性：文件沙箱、异步任务队列、权限、审计记录、生产级超时、持久化和并发保护。

## 安全说明

仅在本机127.0.0.1启动。本原型对上传文件设20MB限制，并限制STEP最大零件数量及候选零件对数量，但还**不具备完整的非可信CAD文件解析沙箱与任务超时隔离能力**。不应直接开放公网，不应承载未经批准的涉密工程图纸。

## 演示案例

安装测试依赖后执行：

```bash
python examples/create_demo_files.py
```

程序在`examples/`目录生成一组**故意包含异常的模拟设计文件**：人工指定的尺寸文字、零长度图元、与BOM不同的材料名称，以及两块静态相交的实体。将三个文件一起上传，即可查看检查结果。演示文件不用于实际生产设计。


## 第三轮NIST工程图纸测试（已通过）

本项目已使用11份独立来源的DXF/STEP文件实施兼容性与规则实测，另用真实NIST支架几何生成两组受控装配干涉案例。当时GitHub Actions通过5项基础测试和7项公开样本测试；这是第三轮历史记录，不能代表当前完整测试数量。详细的图元数量、干涉体积、真实缺陷证据条件及当前不足见 [《第三轮NIST工程图纸实测报告》](docs/第三轮NIST工程图纸实测报告.md)。

**不要将上述12项自动测试通过理解为“真实机械设计错误检出率已达到某个水平”**。公开CAD样本缺少经专家标定的机械设计错误，当前只证明所列文件的解析与部分规则行为。

## 公开真实CAD文件的复验

除项目内人工生成的测试文件外，\`tests/test_public_samples.py\` 会从三个公开仓库按固定提交版本下载真实DXF和STEP样本，核对Git blob散列值后执行解析与审查。**第三方文件不会随本仓库分发。**

- haiha-ux/cad-sample-tests：机械安装板DXF（15条尺寸）、轴承座DXF（22条尺寸）。
- CadQuery/cadquery：二维DXF文件与红色立方体/蓝色圆柱体STEP文件。
- FreeCAD/FreeCAD：原生STEP测试装配文件。
- 利用安装板原始图纸，另外进行一次受控尺寸文字修改，测试系统能否定位异常标注的原始实体句柄。

联网复验：

\`\`\`bash
python -m pip install -e . pytest cadquery
python -m pytest -s -q tests/test_public_samples.py
\`\`\`

GitHub Actions：\`.github/workflows/cad-public-samples.yml\`。测试记录会显示实际解析的图元数量、零件数量、疑似问题数及类别。**这些公开样本主要检验格式兼容、解析能力与异常提示，不能直接证明机械设计审图的高召回率或高精确率。**本项目还缺少经工程专家确认的真实错图和规范的独立验证。
