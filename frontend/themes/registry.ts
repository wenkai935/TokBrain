export const THEME_IDS = [
  "sunset-cloudsea",
  "eastern-mist",
  "ocean-dawn",
] as const;

export type ThemeId = (typeof THEME_IDS)[number];
export type ThemeTone = "light" | "dark";

export type ThemeCopy = {
  brandTagline: string;
  pages: {
    library: string;
    parse: string;
    settings: string;
  };
};

export type ThemeDefinition = {
  id: ThemeId;
  name: string;
  description: string;
  tone: ThemeTone;
  mark: string;
  preview: readonly [string, string, string, string];
  copy: ThemeCopy;
};

const sharedCopy: ThemeCopy = {
  brandTagline: "本地公开内容知识库",
  pages: {
    library: "我的知识库",
    parse: "解析与确认",
    settings: "安全与处理设置",
  },
};

function theme(
  definition: Omit<ThemeDefinition, "copy">,
): ThemeDefinition {
  return {
    ...definition,
    copy: sharedCopy,
  };
}

export const THEMES: readonly ThemeDefinition[] = [
  theme({
    id: "sunset-cloudsea",
    name: "晚霞云海",
    description: "金橙地平线与暮色玻璃",
    tone: "dark",
    mark: "●",
    preview: ["#171a3d", "#342044", "#ffb36b", "#fff0df"],
  }),
  theme({
    id: "eastern-mist",
    name: "东方雾山",
    description: "宣纸留白与现代中文编辑设计",
    tone: "light",
    mark: "山",
    preview: ["#f5f1e7", "#fbf9f2", "#426b5a", "#1f2925"],
  }),
  theme({
    id: "ocean-dawn",
    name: "海洋晨光",
    description: "海岸晨光与磨砂海玻璃",
    tone: "dark",
    mark: "≋",
    preview: ["#041b36", "#0d4261", "#28d8d1", "#f4fbff"],
  }),
] as const;

export const DEFAULT_THEME_ID: ThemeId = "sunset-cloudsea";
export const THEME_STORAGE_KEY = "tokbrain.theme";

const themeMap = new Map<ThemeId, ThemeDefinition>(
  THEMES.map((item) => [item.id, item]),
);

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === "string" && themeMap.has(value as ThemeId);
}

export function getTheme(value: unknown): ThemeDefinition {
  return isThemeId(value)
    ? themeMap.get(value)!
    : themeMap.get(DEFAULT_THEME_ID)!;
}
