# 视觉理解黄金测试集 v1

此目录用于保存视觉理解模型的独立测试集，不参与训练、早停、超参数选择或最佳 Adapter 选择。

整理真实数据时，将图片放入 images/，复制 metadata.template.jsonl 为 metadata.jsonl，再把占位记录替换为真实标注。

## 推荐样本字段

每行一个 JSON 对象。平台当前上传校验实际使用 file_name、question、answer/text 字段，其余字段用于测试集治理、分层统计和评测记录。

建议字段：

- sample_id：测试样本唯一 ID。
- group_id：原图、商品、文档或业务对象分组 ID，用于防止数据泄漏。
- file_name：图片相对路径，例如 images/eval-v1-000001.jpg。
- task_type：image_description、visual_question_answering、ocr、document_understanding 或 structured_extraction。
- domain：商品、工业、文档等业务领域。
- difficulty：easy、medium 或 hard。
- question：评测问题。
- answer：人工确认的标准答案。
- expected_format：text 或 json。
- review_status：unreviewed、reviewed 或 double_checked。

## 首版建议组成

建议准备 100 条左右：

- image_description：25 条。
- visual_question_answering：25 条。
- ocr：20 条。
- document_understanding：15 条。
- structured_extraction：15 条。

如果当前业务没有某一类数据，不要用虚构样本补齐，应在 manifest.json 中标记为 planned。

## 采集原则

1. 测试图片不能来自已经参与训练的图片。
2. 同一原图、同一商品、同一文档、同一对象的多个问题必须使用相同 group_id。
3. group_id 不能同时出现在训练集、验证集和测试集。
4. 测试集优先覆盖真实业务分布，不要只收集容易样本。
5. 每条答案至少经过一名标注人员检查；关键业务样本建议双人复核。
6. OCR 和结构化抽取必须保留精确答案。
7. 涉及隐私、证件、医疗或客户信息的图片必须脱敏并记录授权来源。
8. 图片文件名只使用 ASCII、数字、下划线和连字符。

## 难度定义

- easy：主体清晰，问题直接，答案唯一。
- medium：需要识别多个对象、关系、状态或局部文字。
- hard：低清晰度、复杂布局、隐含关系、长文本或严格 JSON 输出。

## 评测规则

- image_description：人工评分为主，文本相似度只能作为辅助。
- visual_question_answering：准确率和人工判定。
- ocr：字符准确率、编辑距离。
- document_understanding：字段准确率、字段完整率。
- structured_extraction：JSON 可解析率、字段准确率和格式合规率。

测试集一旦发布，应记录图片清单、标注版本、SHA-256、采集时间和评测规则。模型训练完成后只能读取测试集进行最终评估，不能根据测试结果回头修改训练参数；如果修改了测试数据或提示词，必须生成新的测试集版本。

## 当前已整理版本

当前目录已整理 TextVQA validation 的 30 条 OCR 样本：

- 来源：TextVQA validation。
- 标注：官方验证集人工答案，多答案取多数答案作为基准答案，同时保留原始 answers。
- 用途：开发阶段视觉 OCR/VQA 测试。
- 训练用途：未用于本项目训练。
- 正式使用前：仍建议结合你的业务数据补充一套内部黄金测试集。

## 校验命令

整理阶段允许未复核样本：

    python scripts/validate_vision_eval_dataset.py data/vision_understanding_eval_v1 --allow-unreviewed

正式冻结前校验并生成 manifest：

    python scripts/validate_vision_eval_dataset.py data/vision_understanding_eval_v1 --write-manifest

与训练集和验证集检查图片及 group_id 泄漏：

    python scripts/validate_vision_eval_dataset.py data/vision_understanding_eval_v1 --reference-metadata path/to/train/metadata.jsonl --reference-metadata path/to/validation/metadata.jsonl --write-manifest

脚本返回码为 0 才表示数据可以进入下一步；存在错误时返回 1。
