import { api, type GeneratedImageView } from "./api";

export interface GenerateImageBody {
  conversation_id?: number;
  chapter_id?: number;
  selected_text?: string;
  user_refine?: string;
  source_start?: number;
  source_end?: number;
}

export interface GenerateImageResponse extends Omit<GeneratedImageView, "message_id"> {
  message_id: number;
}

export const imageApi = {
  generate: (novelId: string | number, body: GenerateImageBody) =>
    api.post<GenerateImageResponse>(
      `/novels/${novelId}/chat/generate-image`,
      body,
      { timeout: 120000 }
    ),
};
