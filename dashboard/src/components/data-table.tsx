import {
  ColumnDef,
  SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDownIcon, ArrowUpIcon, ArrowUpDownIcon } from "lucide-react";
import { useState } from "react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

interface DataTableProps<T> {
  columns: ColumnDef<T, unknown>[];
  data: T[];
  /** Row key; defaults to the index. */
  getRowId?: (row: T) => string;
  onRowClick?: (row: T) => void;
  empty?: React.ReactNode;
  className?: string;
  initialSorting?: SortingState;
  /** Dimmed while the next page loads (keeps the previous rows visible). */
  loading?: boolean;
}

/** TanStack-table on shadcn's `table` primitives (shadcn data-table guide): client-side
 * sorting, keyboard-reachable rows (Enter/Space activate `onRowClick`). */
export function DataTable<T>({
  columns,
  data,
  getRowId,
  onRowClick,
  empty = "No rows.",
  className,
  initialSorting = [],
  loading,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSorting);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId: getRowId ? (row) => getRowId(row) : undefined,
  });

  return (
    <div className={cn("overflow-x-auto rounded-md border transition-opacity", loading && "opacity-60", className)}>
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((hg) => (
            <TableRow key={hg.id}>
              {hg.headers.map((header) => (
                <TableHead key={header.id} className={cn((header.column.columnDef.meta as ColumnMeta)?.align === "right" && "text-right")}>
                  {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={columns.length} className="h-24 text-center text-muted-foreground">
                {empty}
              </TableCell>
            </TableRow>
          ) : (
            table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                tabIndex={onRowClick ? 0 : undefined}
                role={onRowClick ? "button" : undefined}
                className={cn(onRowClick && "cursor-pointer focus-visible:bg-muted/60 focus-visible:outline-none")}
                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                onKeyDown={
                  onRowClick
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onRowClick(row.original);
                        }
                      }
                    : undefined
                }
              >
                {row.getVisibleCells().map((cell) => (
                  <TableCell
                    key={cell.id}
                    className={cn(
                      (cell.column.columnDef.meta as ColumnMeta)?.align === "right" && "text-right tabular-nums",
                      (cell.column.columnDef.meta as ColumnMeta)?.className,
                    )}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

export interface ColumnMeta {
  align?: "right";
  className?: string;
}

/** Header cell with a sort toggle (shadcn data-table "sortable column" recipe). */
export function SortableHeader<T>({ column, children }: { column: { getIsSorted: () => false | "asc" | "desc"; toggleSorting: (desc?: boolean) => void; columnDef: ColumnDef<T, unknown> }; children: React.ReactNode }) {
  const sorted = column.getIsSorted();
  const Icon = sorted === "asc" ? ArrowUpIcon : sorted === "desc" ? ArrowDownIcon : ArrowUpDownIcon;
  const right = (column.columnDef.meta as ColumnMeta)?.align === "right";
  return (
    <Button
      variant="ghost"
      size="sm"
      className={cn("-mx-2 h-8 gap-1 px-2 font-medium", right && "flex-row-reverse")}
      onClick={() => column.toggleSorting(sorted === "asc")}
      aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"}
    >
      {children}
      <Icon className="size-3.5 text-muted-foreground" />
    </Button>
  );
}
