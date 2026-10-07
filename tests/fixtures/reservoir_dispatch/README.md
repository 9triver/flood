# 调度参考样例

`dispatch_input.csv` 和 `dispatch_output.csv` 分别来自更新包
`flood-updates-20261006-194945/flood/Scheduling model/` 中的
`Dispatch_Input.csv` 和 `Dispatch_Output.csv`。

原样例时间为 `2026/7/21 0:00`，原引擎的独立命令要求
`2026-07-21 00:00:00`。本目录仅规范时间、换行并去掉输入文件的说明行，保留所有
入流和参考计算数值。参考输出对应 `RULE`、初始水位 245.10 m、1 小时步长、
全部剩余时段预见期、独立泄水口能力 20 m³/s，未设置用户最大下泄覆盖。

原测试引用的 `Dispatch_Example_Input.csv` 未包含在更新包内。移植后的命令行
人工控制测试使用此处的入流序列，在临时文件中明确设置目标下泄 15 m³/s、
目标末水位 245.30 m，不依赖缺失的示例文件。
