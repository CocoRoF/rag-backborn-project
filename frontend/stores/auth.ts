"use client";
import { create } from "zustand";

export interface User { id: string; email: string; name: string; role: "user" | "admin"; status: string }

interface AuthState {
  token: string | null;
  user: User | null;
  ready: boolean;
  setAuth: (token: string, user: User) => void;
  clear: () => void;
  setReady: (ready: boolean) => void;
}

export const useAuth = create<AuthState>((set) => ({
  token: null,
  user: null,
  ready: false,
  setAuth: (token, user) => set({ token, user, ready: true }),
  clear: () => set({ token: null, user: null, ready: true }),
  setReady: (ready) => set({ ready }),
}));
