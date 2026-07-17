import Papa from "papaparse";

import type { EntityType } from "../../types/domain";

export interface CsvSummary {
  total: number;
  counts: Record<EntityType, number>;
  sample: Record<string, string>[];
}

const entityAliases: Record<string, EntityType | undefined> = {
  部门: "organization_unit",
  organization_unit: "organization_unit",
  班级: "class",
  class: "class",
  教师: "teacher",
  teacher: "teacher",
  学生: "student",
  student: "student",
};

export function summarizeCsv(file: File): Promise<CsvSummary> {
  return new Promise((resolve, reject) => {
    Papa.parse<Record<string, string>>(file, {
      header: true,
      skipEmptyLines: true,
      complete: ({ data, errors }) => {
        if (errors.length && data.length === 0) {
          reject(new Error("CSV 文件无法解析，请检查格式"));
          return;
        }
        const counts: Record<EntityType, number> = {
          organization_unit: 0,
          class: 0,
          teacher: 0,
          student: 0,
        };
        data.forEach((row) => {
          const rawType = row.entity_type ?? row["实体类型"] ?? row.type ?? "";
          const entityType = entityAliases[rawType.trim()];
          if (entityType) counts[entityType] += 1;
        });
        resolve({ total: data.length, counts, sample: data.slice(0, 5) });
      },
      error: () => reject(new Error("CSV 文件无法读取，请重新选择")),
    });
  });
}
