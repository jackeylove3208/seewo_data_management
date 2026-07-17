import csv
import random
import uuid
from datetime import datetime

# ---------- 固定基础数据 ----------
DEPARTMENTS = [
    {"id": "D01", "name": "教务处", "parent": ""},
    {"id": "D02", "name": "德育处", "parent": ""},
    {"id": "D03", "name": "总务处", "parent": ""},
    {"id": "D04", "name": "高一年级组", "parent": "D01"},
    {"id": "D05", "name": "高二年级组", "parent": "D01"},
    {"id": "D06", "name": "高三年级组", "parent": "D01"},
]

CLASSES = [
    {"id": "C01", "name": "高一(1)班", "grade": "高一"},
    {"id": "C02", "name": "高一(2)班", "grade": "高一"},
    {"id": "C03", "name": "高一(3)班", "grade": "高一"},
    {"id": "C04", "name": "高一(4)班", "grade": "高一"},
    {"id": "C05", "name": "高一(5)班", "grade": "高一"},
    {"id": "C06", "name": "高二(1)班", "grade": "高二"},
    {"id": "C07", "name": "高二(2)班", "grade": "高二"},
    {"id": "C08", "name": "高二(3)班", "grade": "高二"},
    {"id": "C09", "name": "高二(4)班", "grade": "高二"},
    {"id": "C10", "name": "高二(5)班", "grade": "高二"},
    {"id": "C11", "name": "高三(1)班", "grade": "高三"},
    {"id": "C12", "name": "高三(2)班", "grade": "高三"},
    {"id": "C13", "name": "高三(3)班", "grade": "高三"},
    {"id": "C14", "name": "高三(4)班", "grade": "高三"},
    {"id": "C15", "name": "高三(5)班", "grade": "高三"},
]

SUBJECTS = ["语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理", "体育", "音乐", "美术"]

# 姓氏和名字
SURNAMES = ["张", "王", "李", "刘", "陈", "杨", "赵", "黄", "周", "吴", "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗", "梁", "宋", "郑", "谢", "韩", "唐", "冯", "于", "董", "萧", "程", "曹", "袁", "邓", "许", "傅", "沈", "曾", "彭", "吕", "苏", "卢", "蒋", "蔡", "贾", "丁", "魏", "薛", "叶", "阎", "余", "潘", "杜", "戴", "夏", "钟", "汪", "田", "任", "姜", "范", "方", "石", "姚", "谭", "廖", "邹", "熊", "金", "陆", "郝", "孔", "白", "崔", "康", "毛", "邱", "秦", "江", "史", "顾", "侯", "邵", "孟", "龙", "万", "段", "雷", "钱", "汤", "尹", "黎", "易", "常", "武", "乔", "贺", "赖", "龚", "文"]
GIVEN_NAMES = ["伟", "芳", "娜", "秀英", "敏", "静", "丽", "强", "磊", "洋", "勇", "艳", "杰", "倩", "涛", "明", "超", "秀兰", "霞", "平", "刚", "桂英", "涛", "慧", "建", "文", "华", "玉兰", "飞", "玉梅", "鑫", "志强", "桂芳", "丽华", "志明", "海燕", "晓峰", "淑珍", "桂兰", "玉珍", "海涛", "秀珍", "志刚", "桂香", "玉英", "海霞", "秀芳", "志文", "桂珍", "玉芳", "海燕", "秀英", "志强", "桂兰", "玉珍", "海涛", "秀珍", "志刚", "桂香", "玉英", "海霞", "秀芳", "志文", "桂珍", "玉芳"]

def random_name():
    return random.choice(SURNAMES) + random.choice(GIVEN_NAMES)

def random_phone():
    return f"1{random.choice(['3','5','7','8','9'])}{''.join([str(random.randint(0,9)) for _ in range(9)])}"

def random_email(name):
    return f"{name.lower()}@example.com"

def random_subject():
    return random.choice(SUBJECTS)

# ---------- 生成魔方数据 ----------
def generate_mofa_data():
    records = []
    # 1. 部门
    for dept in DEPARTMENTS:
        records.append({
            "entity_type": "部门",
            "id": dept["id"],
            "name": dept["name"],
            "parent_id": dept["parent"],
            "grade": "",
            "class_name": "",
            "subject": "",
            "phone": "",
            "email": "",
            "extra": ""
        })
    # 2. 班级
    for cls in CLASSES:
        records.append({
            "entity_type": "班级",
            "id": cls["id"],
            "name": cls["name"],
            "parent_id": "",  # 班级无上级
            "grade": cls["grade"],
            "class_name": cls["name"],
            "subject": "",
            "phone": "",
            "email": "",
            "extra": ""
        })
    # 3. 教师（每个部门分配2~4人）
    teacher_id = 1
    for dept in DEPARTMENTS:
        num = random.randint(2, 4)
        for _ in range(num):
            name = random_name()
            records.append({
                "entity_type": "教师",
                "id": f"T{teacher_id:03d}",
                "name": name,
                "parent_id": dept["id"],
                "grade": "",
                "class_name": "",
                "subject": random_subject(),
                "phone": random_phone(),
                "email": random_email(name),
                "extra": ""
            })
            teacher_id += 1
    # 4. 学生（每个班级25~35人）
    student_id = 1
    for cls in CLASSES:
        num = random.randint(25, 35)
        for _ in range(num):
            name = random_name()
            records.append({
                "entity_type": "学生",
                "id": f"S{student_id:04d}",
                "name": name,
                "parent_id": cls["id"],  # 所属班级
                "grade": cls["grade"],
                "class_name": cls["name"],
                "subject": "",
                "phone": random_phone(),
                "email": random_email(name),
                "extra": f"家长: {random_name()}"
            })
            student_id += 1
    return records

# ---------- 生成第三方数据（基于魔方，引入差异） ----------
def generate_third_party_data(mofa_records):
    # 深拷贝并修改
    third = [r.copy() for r in mofa_records]
    # 引入差异：
    # 1. 删除某些学生（随机删5个）
    students = [r for r in third if r["entity_type"] == "学生"]
    to_remove = random.sample(students, min(5, len(students)))
    for r in to_remove:
        third.remove(r)
    # 2. 修改某些教师的部门（随机选3个教师，改部门）
    teachers = [r for r in third if r["entity_type"] == "教师"]
    if len(teachers) >= 3:
        for t in random.sample(teachers, 3):
            # 换一个不同的部门
            dept_ids = [d["id"] for d in DEPARTMENTS if d["id"] != t["parent_id"]]
            if dept_ids:
                t["parent_id"] = random.choice(dept_ids)
    # 3. 修改某些学生的班级（随机选5个学生，换班级）
    students_third = [r for r in third if r["entity_type"] == "学生"]
    if len(students_third) >= 5:
        for s in random.sample(students_third, 5):
            class_ids = [c["id"] for c in CLASSES if c["id"] != s["parent_id"]]
            if class_ids:
                new_class = random.choice(class_ids)
                s["parent_id"] = new_class
                # 同时更新班级名称和年级（从CLASSES中找）
                for c in CLASSES:
                    if c["id"] == new_class:
                        s["grade"] = c["grade"]
                        s["class_name"] = c["name"]
                        break
    # 4. 修改某些班级的名称（比如高一(1)班变成"2024级1班"）
    for r in third:
        if r["entity_type"] == "班级" and r["id"] in ["C01", "C02"]:
            r["name"] = r["name"].replace("高一(1)班", "2024级1班") if r["id"] == "C01" else r["name"]
            r["name"] = r["name"].replace("高一(2)班", "2024级2班") if r["id"] == "C02" else r["name"]
            r["class_name"] = r["name"]  # 同步
    # 5. 增加一个冗余教师（第三方有，魔方没有）
    extra_teacher = {
        "entity_type": "教师",
        "id": "T999",
        "name": "冗余教师",
        "parent_id": "D02",
        "grade": "",
        "class_name": "",
        "subject": "体育",
        "phone": "13800138000",
        "email": "redundant@example.com",
        "extra": "冗余"
    }
    third.append(extra_teacher)
    # 6. 增加一个冗余学生
    extra_student = {
        "entity_type": "学生",
        "id": "S9999",
        "name": "冗余学生",
        "parent_id": "C03",
        "grade": "高一",
        "class_name": "高一(3)班",
        "subject": "",
        "phone": "13800138001",
        "email": "redundant_stu@example.com",
        "extra": "冗余"
    }
    third.append(extra_student)
    return third

# ---------- 写入CSV ----------
def write_csv(filename, records):
    fieldnames = ["entity_type", "id", "name", "parent_id", "grade", "class_name", "subject", "phone", "email", "extra"]
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)

if __name__ == "__main__":
    random.seed(42)  # 固定随机种子，便于复现
    mofa = generate_mofa_data()
    third = generate_third_party_data(mofa)
    write_csv("mofa_data.csv", mofa)
    write_csv("third_party_data.csv", third)
    print(f"✅ 生成完成: mofa_data.csv ({len(mofa)} 条), third_party_data.csv ({len(third)} 条)")
