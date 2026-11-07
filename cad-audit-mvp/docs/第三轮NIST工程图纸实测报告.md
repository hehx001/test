# 第三轮NIST工程图纸实测报告

**核验时间**：2026年11月6日（美国太平洋时间；GitHub Actions日志为11月7日UTC）  
**源码仓库**：[hehx001/test · PR #1](https://github.com/hehx001/test/pull/1)（开发分支：`feat/mechanical-cad-audit-mvp`）  
**最终复验记录**：[GitHub Actions Run 42321004131](https://github.com/hehx001/test/actions/runs/42321004131)  
**运行环境**：GitHub Ubuntu、Python 3.11、ezdxf、cadquery / Open CASCADE

## 一、实测范围

共采用**11份来自独立公开项目的原始CAD文件**，另基于真实文件实施3类受控异常验证：修改DXF尺寸文字、使用NIST支架构造实体重叠、使用同一支架构造完全分离的装配。

| 来源 | 文件 | 类型 | 核查结果 |
|---|---|---|---|
| haiha-ux/cad-sample-tests | CST-A-001_mounting_plate.dxf | DXF | 15条尺寸；79条直线；8个圆。合法尺寸前后缀未产生数值冲突误报 |
| haiha-ux/cad-sample-tests | CST-C-001_step_bearing.dxf | DXF | 22条尺寸；21条可比对数值相符；1条复合加工注释尚不能机器确认 |
| CadQuery | genshi.dxf | DXF | 成功读取，含12条SPLINE与3条LWPOLYLINE；样条曲线仍未在二维简化预览中完整呈现 |
| CadQuery | red_cube_blue_cylinder.step | STEP | 读取2个零件；未检出静态实体交集 |
| FreeCAD | as1-ac-214_small.stp | STEP | 读取5个零件；4组包围盒候选，未检出静态实体交集 |
| NIST | hole.dxf | DXF | 8个图块引用；其中5个为空图块，成功展开56条直线与4个圆；展开失败0 |
| NIST | step.dxf | DXF | 8个图块引用；其中5个为空图块，成功展开42条直线；展开失败0 |
| NIST | gear.stp | STEP | 读取1个零件 |
| NIST | disc.stp | STEP | 读取1个零件 |
| NIST | bracket1-Part.stp | STEP | 读取1个零件 |
| NIST | piston.stp | STEP | 读取1个零件 |

**NIST工程模型源目录**：https://github.com/usnistgov/engineering-design-models  
**NIST固定源提交**：`dc4ed1c352bfa7fa0976c6abc6074a7a40aa1d5a`  
**其他来源、固定提交与原始文件SHA**：参见 `tests/test_public_samples.py` 的 `SAMPLES` 定义。所有第三方文件均由测试代码按固定版本下载并校验Git blob SHA，不随本仓库重新分发。

## 二、已发现并修复的实际问题

### 1. 合法尺寸表示被过度提示

最初版本把人工修改过显示文字的尺寸一律提示复核。实测轴承座图纸含英制分数尺寸，导致22条尺寸全部被提示；安装板中含 `4x <> THRU` 等标注，也被提示。

改进后先识别可安全解析的数值、分数与前后缀，再与几何测量进行比较。最新复验结果：安装板未产生DXF-001提示；轴承座21条尺寸数值相符，1条复杂注释保留待核查状态。

### 2. 图块内部图元未被检查

两份NIST历史DXF将实际几何存放在`INSERT`引用的`BLOCK`中，原版只能读取8个引用，无法扫描真实线条。

增加非破坏性的虚拟图元展开功能，支持多层引用、空间变换及图元数量限制。实测分别恢复56条线＋4个圆、42条线。5个原本就没有内容的图块单独计数，不作为失败。未能展开的有效引用将单独报告为`DXF-006`，避免错误报告检查完整。

## 三、受控验证：检出和非检出都必须正确

**尺寸异常正例**：在公开安装板图纸副本中，将已有关联尺寸的显示文字改成`9999.99`。程序报告`DXF-001`并准确找到原始实体句柄`94`。

**实体干涉正例**：读取NIST真实支架STEP，将同一支架复制为两个同位置装配零件。程序报告1处实体交集，几何交集体积为`81489.462498`（模型长度单位的三次方）。

**实体不干涉反例**：将第二个支架移至远离第一个支架的位置。程序返回2个零件、0组包围盒相交候选、0条干涉报告。

上述两组装配是**利用真实支架形状人为设置的测试条件**，不能把它们描述成NIST原始设计存在缺陷。

## 四、可复核的测试结论

最终GitHub Actions复验：

- 5项基础自动测试：全部通过；
- 7项公开CAD样本与受控测试：全部通过；
- **合计12项测试通过**；
- 原始公开文件总数为11份，测试项数和图纸份数不是同一指标。

请访问 [GitHub Actions Run 42321004131](https://github.com/hehx001/test/actions/runs/42321004131) 的`verify`作业查看实际日志，搜索`REAL_DXF`、`REAL_STEP`、`NIST_DXF`、`NIST_STEP`、`MUTATION`和`NIST_CONTROLLED`核验详细记录。

## 五、尚未通过的工程能力验证

1. **没有独立人工标注的真实错图集合**：尚不能计算机械设计错误召回率、精确率或专家采纳率。
2. **二维预览仍有限制**：如SPLINE、HATCH、部分文字与复杂注释不能完整绘制；程序已记录图元类别，不能声称完整复现原CAD图。
3. **STEP静态装配检查范围有限**：真实NIST文件大多是单零件模型，只有受控案例检验了干涉正例和反例。未覆盖复杂运动、应力、公差链及预期过盈的自动甄别。
4. **标准和设计任务约束尚未机器化**：不能直接裁定尺寸、公差或材料选择是否满足具体企业设计要求。
5. **DWG原生读取、完整GD&T、图纸与三维特征关联**尚未开发。

## 六、下一阶段优先任务

按实际工程价值排序，建议首先使用具有明确人工审核结论的机械图纸建立缺陷样本；其次增加二维尺寸—三维几何关系校核；再次采用NIST PMI验证测试案例扩展语义公差解析。每个规则分别统计检出、误报和未检查范围，优先修复高风险漏检。

NIST官方公开资源：

- [Engineering Design Model Repository](https://github.com/usnistgov/engineering-design-models)
- [MBE PMI Validation and Conformance Testing](https://www.nist.gov/ctl/smart-connected-systems-division/smart-connected-manufacturing-systems-group/mbe-pmi-validation)
- [Download Free CAD Models, STEP Files, and Test Results](https://www.nist.gov/ctl/smart-connected-systems-division/smart-connected-manufacturing-systems-group/mbe-pmi-0)

## 七、复现

在开发分支的`cad-audit-mvp`目录执行：

```bash
python -m pip install -e . pytest cadquery httpx==0.28.1
python -m pytest -q tests/test_audit.py
python -m pytest -s -q tests/test_public_samples.py
```

第二条测试需网络访问公开GitHub原始文件。文件下载均校验指定SHA，保证再次测试所用文件一致。
