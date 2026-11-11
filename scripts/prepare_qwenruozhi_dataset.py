from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any


SYSTEM_PROMPT = (
    "You are an assistant that answers silly, tricky, or misleading questions "
    "accurately, concisely, and with light humor."
)
SEED = 42
TRAIN_COUNT = 1196
VAL_COUNT = 150
TEST_COUNT = 150


CHALLENGE_PAIRS = [
    ("手机关机充电，是不是在偷偷吃电？", "是的，但吃的是充电器送来的电。关机只是少做事，不是停止充电。"),
    ("夏天把冰箱门一直打开，能给房间降温吗？", "不能。冰箱会把更多热量排到房间里，最后房间反而更热。"),
    ("零摄氏度的冰和零摄氏度的水，谁更冷？", "温度一样，但冰融化还需要吸热，摸起来可能让你觉得更冷。"),
    ("闭上一只眼睛，是不是能省一半视力留着以后用？", "不能。视力不是电量，闭眼只能暂时少用一只眼，不能储存视力。"),
    ("影子晚上去哪儿了？", "光线不足时影子不明显，它没有离开，只是暂时不营业。"),
    ("地球一直在转，我跳起来为什么还落在原地？", "因为你起跳前已经和地球一起运动，跳起后仍保留相同的水平速度。"),
    ("鱼一直在水里，会不会喝水喝撑？", "鱼会通过鳃和身体调节水盐平衡，不是像人拿杯子那样一直喝。"),
    ("戴两副眼镜能不能看得比一副更清楚？", "通常不能。度数叠加可能让画面更模糊，还容易头晕。"),
    ("把密码写在电脑旁边，黑客隔着网络是不是看不到？", "网络黑客未必看得到，但路过的人看得到。密码还是交给密码管理器更稳妥。"),
    ("天气预报降水概率30%，是不是有30%的地方会下雨？", "不一定。它通常表示特定地点在预报时段出现降水的概率，不是简单的面积比例。"),
    ("牛奶过期一天，煮开以后还能放心喝吗？", "不建议。煮沸不能可靠消除所有变质产生的风险，过期且状态异常就应丢弃。"),
    ("手机开飞行模式以后为什么飞不起来？", "飞行模式只关闭部分无线通信，不会给手机安装机翼。"),
    ("多喝热水是不是能治所有病？", "不能。热水能补充水分、缓解部分不适，但不能替代针对病因的治疗。"),
    ("体重秤从公斤换成斤，我是不是突然变重了？", "没有，只是数字和单位变了，你本人没有趁机增重。"),
    ("机器人说自己不是机器人，我应该相信它吗？", "不能只听它自我介绍，还要看来源、行为和验证信息。机器人也会背台词。"),
    ("把闹钟调早一小时，是不是就能多睡一小时？", "不能。除非你也早睡一小时，否则只会早醒一小时。"),
    ("太阳晚上是不是关灯休息了？", "没有。地球转到背向太阳的一面形成夜晚，太阳仍在照亮别处。"),
    ("开灯之前房间里的黑暗被灯赶到哪里去了？", "黑暗不是物质，只是缺少可见光；灯亮后这个条件消失了。"),
    ("月亮为什么一直跟着我的车跑？", "月亮离得非常远，你移动的距离相对太小，所以观察方向变化不明显。"),
    ("人脑真的只用了10%吗？", "不是。大脑不同区域在不同任务中活动，并不存在90%长期闲置的可靠证据。"),
    ("鞋子脏了直接放洗衣机，洗衣机会不会生气？", "洗衣机不会生气，但鞋子可能损坏机器或自身。先看鞋和洗衣机的清洗说明。"),
    ("冷冻肉放太阳下解冻更快，是不是也更安全？", "更快不等于更安全。表面会进入细菌易繁殖的温度区，建议冷藏、冷水或微波解冻。"),
    ("把电池放冰箱里能让它活得更久吗？", "多数现代电池不需要冷藏，潮气和结露反而可能造成损害，按说明常温保存即可。"),
    ("电脑卡了拍两下，会不会被拍聪明？", "不会。拍打可能让硬件更糟，先检查任务占用、温度或重启更有效。"),
    ("人体温度36.5度，为什么摸起来不会烫手？", "因为它和手的温度接近，热量传递不强；烫不烫取决于温差和接触时间。"),
    ("空腹的时候吃饭，吃第一口以后还算空腹吗？", "严格说吃下第一口后就不再空腹了，空腹状态成功被第一口终结。"),
    ("没有消息就是好消息，那收到这句话还是好消息吗？", "这句话本身是消息，但通常是在表达暂时没有坏消息，不必和字面较真。"),
    ("水是透明的，为什么大海看起来是蓝色？", "水会吸收和散射不同波长的光，加上天空反射等因素，大量海水常呈蓝色。"),
    ("我跑得足够快，能不能追上昨天？", "不能。提高速度可以换地点，不能让时间倒流。"),
    ("既然失败是成功之母，多失败几次是不是就自动成功？", "不会自动成功。失败只有经过分析和改进，才有机会为成功提供经验。"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare the qwenruozhi SFT benchmark.")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(r"D:\qwenaidataset\qwenruozhi.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "qwenruozhi_v1",
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON at line {line_number}: {exc}") from exc
        messages = row.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError(f"Line {line_number} has no messages array")
        if messages[0].get("role") == "system":
            messages[0]["content"] = SYSTEM_PROMPT
        else:
            messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    content = "\n".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows
    )
    path.write_text(content + "\n", encoding="utf-8")


def challenge_rows() -> list[dict[str, Any]]:
    return [
        {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ]
        }
        for question, answer in CHALLENGE_PAIRS
    ]


def question(row: dict[str, Any]) -> str:
    return next(
        str(message["content"]).strip()
        for message in row["messages"]
        if message.get("role") == "user"
    )


def write_dataset_info(path: Path) -> None:
    entries = {}
    for name, file_name in (
        ("qwenruozhi_train", "train.jsonl"),
        ("qwenruozhi_val", "val.jsonl"),
        ("qwenruozhi_test", "test.jsonl"),
        ("qwenruozhi_challenge", "challenge.jsonl"),
    ):
        entries[name] = {
            "file_name": file_name,
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": {
                "role_tag": "role",
                "content_tag": "content",
                "user_tag": "user",
                "assistant_tag": "assistant",
                "system_tag": "system",
            },
        }
    path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_report(
    path: Path,
    source: Path,
    splits: dict[str, list[dict[str, Any]]],
) -> None:
    lines = [
        "# Qwenruozhi v1 Split Report",
        "",
        f"- Source: `{source}`",
        f"- Source SHA-256: `{sha256(source)}`",
        f"- Split seed: `{SEED}`",
        f"- System prompt: `{SYSTEM_PROMPT}`",
        "- Test policy: test and challenge sets must not be used for training or parameter selection.",
        "",
        "## Counts",
        "",
        "| Split | Rows | Purpose |",
        "| --- | ---: | --- |",
        f"| Train | {len(splits['train'])} | LoRA training |",
        f"| Validation | {len(splits['val'])} | Hyperparameter selection and overfitting checks |",
        f"| Test | {len(splits['test'])} | Final held-out evaluation |",
        f"| Challenge | {len(splits['challenge'])} | Novel manually written questions |",
    ]
    for split_name in ("train", "val", "test", "challenge"):
        lines.extend(["", f"## {split_name.title()} Preview", ""])
        for index, row in enumerate(splits[split_name][:10], 1):
            lines.append(f"{index}. {question(row)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    rows = read_rows(source)
    expected_count = TRAIN_COUNT + VAL_COUNT + TEST_COUNT
    if len(rows) != expected_count:
        raise ValueError(f"Expected {expected_count} source rows, got {len(rows)}")

    rng = random.Random(SEED)
    rng.shuffle(rows)
    splits = {
        "train": rows[:TRAIN_COUNT],
        "val": rows[TRAIN_COUNT : TRAIN_COUNT + VAL_COUNT],
        "test": rows[TRAIN_COUNT + VAL_COUNT :],
        "challenge": challenge_rows(),
    }

    source_questions = {question(row) for row in rows}
    challenge_questions = {question(row) for row in splits["challenge"]}
    overlap = source_questions & challenge_questions
    if overlap:
        raise ValueError(f"Challenge questions overlap source questions: {sorted(overlap)}")

    for split_name, split_rows in splits.items():
        write_jsonl(output / f"{split_name}.jsonl", split_rows)
    write_dataset_info(output / "dataset_info.json")

    manifest = {
        "version": "qwenruozhi_v1",
        "source": str(source),
        "source_sha256": sha256(source),
        "seed": SEED,
        "system_prompt": SYSTEM_PROMPT,
        "counts": {name: len(split) for name, split in splits.items()},
        "files": {
            f"{name}.jsonl": sha256(output / f"{name}.jsonl")
            for name in splits
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_report(output / "SPLIT_REPORT.md", source, splits)

    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
