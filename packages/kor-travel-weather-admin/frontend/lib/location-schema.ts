import { z } from "zod";

export const locationSchema = z.object({
  location_id: z.string().trim().min(1),
  name: z.string().trim().min(1),
  latitude: z.number().min(-90).max(90),
  longitude: z.number().min(-180).max(180),
  nx: z.number().int().positive(),
  ny: z.number().int().positive(),
});
export type LocationFields = z.infer<typeof locationSchema>;
