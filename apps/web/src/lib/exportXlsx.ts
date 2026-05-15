// Browser-side Excel export helper. exceljs is lazy-loaded on first
// call so it doesn't bloat the route bundle for users who never click
// the download button.

export type XlsxCell = string | number | null | undefined;

export type XlsxSheet = {
  name: string;
  // First row is treated as the header (bold, frozen).
  rows: XlsxCell[][];
  // Optional per-column widths in characters; if omitted we auto-size.
  columnWidths?: number[];
};

/**
 * Build an .xlsx workbook from one or more sheets and trigger a browser
 * download. Numeric cells stay numeric (so Excel can re-sum them) — pass
 * pre-formatted strings only when you want a literal display value.
 */
export async function downloadXlsx(
  filename: string,
  sheets: XlsxSheet[],
): Promise<void> {
  const { default: ExcelJS } = await import('exceljs');
  const wb = new ExcelJS.Workbook();
  wb.creator = 'Fondok';
  wb.created = new Date();

  for (const sheet of sheets) {
    const ws = wb.addWorksheet(sheet.name.slice(0, 31)); // Excel cap.
    sheet.rows.forEach(row => ws.addRow(row));

    if (sheet.rows.length > 0) {
      ws.getRow(1).font = { bold: true };
      ws.views = [{ state: 'frozen', ySplit: 1 }];
    }

    const widths = sheet.columnWidths;
    if (widths) {
      widths.forEach((w, i) => { ws.getColumn(i + 1).width = w; });
    } else {
      // Auto-size: max content length in column, capped to keep
      // workbooks readable.
      const colCount = Math.max(0, ...sheet.rows.map(r => r.length));
      for (let i = 0; i < colCount; i++) {
        const maxLen = Math.max(
          10,
          ...sheet.rows.map(r => {
            const cell = r[i];
            if (cell == null) return 0;
            return String(cell).length;
          }),
        );
        ws.getColumn(i + 1).width = Math.min(maxLen + 2, 40);
      }
    }
  }

  const buffer = await wb.xlsx.writeBuffer();
  const blob = new Blob([buffer], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename.endsWith('.xlsx') ? filename : `${filename}.xlsx`;
  a.click();
  URL.revokeObjectURL(url);
}
