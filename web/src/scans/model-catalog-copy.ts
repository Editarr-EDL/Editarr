/** Static, factual picker copy for each scan model id the backend may advertise. */

export interface ModelCopy {
  name: string;
  variant?: string;
  description: string;
  detects: string[];
  reports: string[];
  bestFor?: string;
  badges: string[];
}

const videoFamilies = ["Drugs, alcohol, smoking or gambling", "Nudity", "Sexual content", "Violence"];
const transcriptReports = ["Timed transcript"];
const filterReports = ["Suggested mute ranges"];
const descriptionReports = ["Scene description", "Severity"];

export const MODEL_COPY: Record<string, ModelCopy> = {
  whisper: {
    name: "Whisper", variant: "faster-whisper",
    description: "Local speech-to-text with the Whisper base model.",
    detects: ["Spoken dialogue"], reports: transcriptReports,
    bestFor: "Any supported server; uses CUDA on Windows and Linux when available", badges: [],
  },
  mlx_whisper: {
    name: "Whisper", variant: "mlx-whisper",
    description: "The Whisper base model running on MLX.",
    detects: ["Spoken dialogue"], reports: transcriptReports,
    bestFor: "Apple silicon Macs", badges: ["Apple silicon"],
  },
  apple_speech: {
    name: "Apple Speech",
    description: "macOS speech recognition on the Editarr server. macOS may download speech assets for the track’s language.",
    detects: ["Spoken dialogue"], reports: transcriptReports,
    bestFor: "Mac servers running macOS 26 or later", badges: ["macOS only"],
  },
  better_profanity: {
    name: "better-profanity", variant: "word list",
    description: "Flags profanity in the transcript with the package’s bundled word list.",
    detects: ["Profanity", "Your Deny words"], reports: filterReports,
    bestFor: "A built-in default that needs no download", badges: ["Word list"],
  },
  glin_profanity: {
    name: "glin-profanity",
    description: "Flags profanity with the multilingual dictionaries bundled in the package.",
    detects: ["Profanity", "Your Deny words"], reports: filterReports,
    bestFor: "Word-list checks beyond English", badges: ["Multilingual"],
  },
  alt_profanity_check: {
    name: "alt-profanity-check",
    description: "Flags profanity with the package’s bundled text classifier.",
    detects: ["Profanity", "Your Deny words"], reports: filterReports,
    bestFor: "Classifier-based checks instead of a word list", badges: ["Classifier"],
  },
  qwen: {
    name: "Qwen", variant: "multilingual",
    description: "Checks the transcript for profanity and blasphemy in any language and rates each finding.",
    detects: ["Profanity", "Blasphemy"], reports: ["Category", "Severity"],
    bestFor: "Non-English or mixed-language audio", badges: ["Multilingual", "Apple silicon"],
  },
  "qwen3-vl-8b": {
    name: "Qwen3-VL 8B", variant: "MLX 4-bit",
    description: "Vision-language model that asks yes/no questions about each frame.",
    detects: videoFamilies, reports: ["Flagged scenes by family"],
    bestFor: "Pairing with the 8B description model, which shares its weights", badges: ["Apple silicon"],
  },
  "qwen3-vl-4b": {
    name: "Qwen3-VL 4B", variant: "MLX 4-bit",
    description: "The smaller Qwen3-VL detector, asking the same questions with a smaller download.",
    detects: videoFamilies, reports: ["Flagged scenes by family"],
    bestFor: "Pairing with the 4B description model, which shares its weights", badges: ["Apple silicon"],
  },
  "qwen38-27b": {
    name: "Qwen3.8-27B", variant: "MLX 4-bit",
    description: "Vision-language model that asks yes/no questions about each frame.",
    detects: videoFamilies, reports: ["Flagged scenes by family"],
    bestFor: "Pairing with the Qwen3.8-27B description model, which shares its weights", badges: ["Apple silicon"],
  },
  nudenet: {
    name: "NudeNet",
    description: "Detector bundled with its package that flags nudity in each frame.",
    detects: ["Nudity"], reports: ["Nudity findings"],
    bestFor: "Nudity checks on any supported server", badges: ["Nudity only"],
  },
  image_safety_gate: {
    name: "Image Safety Gate",
    description: "Scores each frame with a WD tagger, a CLIP safety checker and an NSFL classifier.",
    detects: ["Nudity", "Graphic violence"], reports: ["Nudity and violence findings"],
    bestFor: "A layered safe/unsafe check", badges: [],
  },
  siglip2_moderation_nsfw: {
    name: "SigLIP2 Moderation", variant: "NSFW",
    description: "SigLIP2 image embeddings with a small NSFW classification head.",
    detects: ["Nudity"], reports: ["Nudity findings"],
    bestFor: "Shares its SigLIP2 encoder with the Violence head", badges: ["Classifier"],
  },
  siglip2_moderation_violence: {
    name: "SigLIP2 Moderation", variant: "Violence",
    description: "SigLIP2 image embeddings with a small violence classification head.",
    detects: ["Violence"], reports: ["Violence findings"],
    bestFor: "Shares its SigLIP2 encoder with the NSFW head", badges: ["Classifier"],
  },
  shieldstral: {
    name: "Shieldstral", variant: "3B INT8",
    description: "Safety vision model that answers yes/no policy questions about each frame.",
    detects: ["Sexual content", "Nudity", "Violence"], reports: ["Flagged scenes by family"],
    bestFor: "Policy-style screening of sexual content and violence", badges: ["Apple silicon"],
  },
  "d1-3b": {
    name: "d1-3B", variant: "Liquid AI",
    description: "Decision model that answers typed questions in one pass without generating text.",
    detects: videoFamilies, reports: ["Flagged scenes by family", "Severity"],
    bestFor: "Detector severity; runs on Metal on Macs and CPU elsewhere", badges: ["LFM Open License"],
  },
  "qwen3-vl-8b-instruct-4bit": {
    name: "Qwen3-VL 8B", variant: "MLX 4-bit",
    description: "Describes flagged scenes and rates their severity.",
    detects: ["Flagged scenes"], reports: descriptionReports,
    bestFor: "Pairing with the Qwen3-VL 8B video model, which shares its weights", badges: ["Apple silicon"],
  },
  "qwen3-vl-4b-instruct-4bit": {
    name: "Qwen3-VL 4B", variant: "MLX 4-bit",
    description: "Describes flagged scenes and rates their severity with a smaller download.",
    detects: ["Flagged scenes"], reports: descriptionReports,
    bestFor: "Pairing with the Qwen3-VL 4B video model, which shares its weights", badges: ["Apple silicon"],
  },
  "qwen38-27b-instruct-4bit": {
    name: "Qwen3.8-27B", variant: "MLX 4-bit",
    description: "Checks transcripts for objectionable language, describes flagged scenes, and rates severity.",
    detects: ["Flagged scenes", "Objectionable language"], reports: descriptionReports,
    bestFor: "Pairing with the Qwen3.8-27B video model, which shares its weights", badges: ["Apple silicon"],
  },
};

/** Safe copy for a model id the catalog doesn't know yet, named from the backend's display name. */
export function fallbackCopy(displayName: string): ModelCopy {
  return { name: displayName, description: "Provided by this Editarr server.", detects: [], reports: [], badges: [] };
}
