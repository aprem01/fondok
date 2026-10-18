import { redirect } from 'next/navigation';

// The former Data Library was a stub: three tabs of empty lists whose "Add"
// modals and kebab actions toasted success without saving anything. Comp sets
// and market data come from the STR and market studies uploaded on each deal;
// the one backed library is the Portfolio Library under Settings, so that is
// where this route lands. Old links and bookmarks keep working.
export default function DataLibraryRedirect() {
  redirect('/settings/portfolio-library');
}
