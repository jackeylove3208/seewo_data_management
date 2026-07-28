export function candidateLabel(index: number) {
  return index < 26 ? String.fromCharCode(65 + index) : String(index + 1);
}
