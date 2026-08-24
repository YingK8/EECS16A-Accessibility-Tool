%% latexa11y-assignment.cls
%% Accessible homework / problem-set class in the EECS 16A house style.
%%
%%   \documentclass[sol]{latexa11y-assignment}
%%
%% Typesets identically to an ee16.sty document -- same text block, same fonts,
%% same masthead, same "1." question labels and inline blue "Solution:" -- and
%% additionally produces a tagged PDF with an H1-H4 heading tree and bookmarks.
%% Authors keep writing \qns, \q, \qitem, \sol and qunlist.
%%
%% Class options replace the corpus convention of keeping two near-identical
%% driver files (prob9.tex and sol9.tex differing only in whether \sol expands
%% to nothing). One source, two builds.
%%
%%   prob         student version, solutions hidden          (default)
%%   sol          full solutions
%%   ans          answers only, no worked solutions
%%   guidance     also show staff-only guidance blocks
%%   noreserve    do not reserve blank space where a solution is hidden
%%   housecolors  use the course's original solution colours, including the
%%                ones that fail WCAG AA contrast
%%   draft        downgrade unfilled-alt errors to warnings (migration only)

\NeedsTeXFormat{LaTeX2e}[2020-10-01]
\ProvidesClass{latexa11y-assignment}[2026-08-11 v0.2.0 Accessible assignment]

\newif\ifaccess@sol         \access@solfalse
\newif\ifaccess@ans         \access@ansfalse
\newif\ifaccess@guidance    \access@guidancefalse
\newif\ifaccess@reserve     \access@reservetrue
\newif\ifaccess@draft       \access@draftfalse
\newif\ifaccess@house       \access@housefalse

\DeclareOption{prob}{\access@solfalse\access@ansfalse}
\DeclareOption{sol}{\access@soltrue\access@anstrue}
\DeclareOption{ans}{\access@anstrue}
\DeclareOption{guidance}{\access@guidancetrue}
\DeclareOption{noreserve}{\access@reservefalse}
\DeclareOption{housecolors}{\access@housetrue}
\DeclareOption{draft}{\access@drafttrue}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{article}}
\ProcessOptions\relax

\LoadClass[11pt]{article}

%% No `geometry` here: latexa11y-doc sets the text block by raw dimension
%% assignment, exactly as ee16.sty does, and geometry would recompute it from
%% margins into something different. No `enumitem` either -- it changes list
%% spacing, and the house style depends on the stock list parameters.
\RequirePackage{hyperref}
\RequirePackage{bookmark}

\ifaccess@house
  \PassOptionsToPackage{housecolors}{latexa11y-doc}
\fi
\RequirePackage{latexa11y-doc}

\ifaccess@sol\accessshowsolutions\fi
\ifaccess@ans\accessshowanswers\fi
\ifaccess@guidance\accessshowguidance\fi
\ifaccess@reserve\else\accessnoreserve\fi
\ifaccess@draft\accesssetup{strict=false}\fi

\endinput
