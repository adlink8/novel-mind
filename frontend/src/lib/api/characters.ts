/**
 * 人物 API。
 */

import { api } from "./client";

export interface Character {
  id: string;
  novel_id: string;
  name: string;
  aliases: string[];
  description: string;
  personality: Record<string, string>;
  role: "protagonist" | "antagonist" | "supporting" | "minor";
  first_appearance: number;
  stats: Record<string, number>;
}

export interface CharacterRelation {
  id: string;
  source_character_id: string;
  target_character_id: string;
  relation_type: string;
  description: string;
  strength: number;
}

export const charactersApi = {
  getCharacters: (novelId: string) => api.get<Character[]>(`/characters/${novelId}`),
  getRelations: (novelId: string) => api.get<CharacterRelation[]>(`/characters/${novelId}/relations`),
  extractCharacters: (novelId: string) => api.post(`/characters/${novelId}/extract`),
};
