import apiClient from './index';

export type ExtractFromImageResponse = {
  codes: string[];
  rawText?: string;
};

export const stocksApi = {
  async extractFromImage(file: File): Promise<ExtractFromImageResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await apiClient.post<{ codes: string[]; raw_text?: string }>(
      '/api/v1/stocks/extract-from-image',
      formData,
      {
        headers: { 'Content-Type': undefined } as Record<string, string | undefined },
        timeout: 60000, // Vision API can be slow; 60s
      },
    );

    return {
      codes: response.data.codes ?? [],
      rawText: response.data.raw_text,
    };
  },
};
