%% latexa11y-worksheet.cls
%% Accessible discussion-worksheet class in the EECS 16A house style.
%%
%% Same structure as an assignment; the difference is that worksheets are
%% published in three variants rather than two, which the corpus expressed as
%% three sibling driver files (dis09A / ans09A / sol09A) sharing one body.
%%
%%   blank        nothing revealed, working space reserved   (default)
%%   ans          answers only
%%   sol          full solutions
%%   housecolors  use the course's original solution colours

\NeedsTeXFormat{LaTeX2e}[2020-10-01]
\ProvidesClass{latexa11y-worksheet}[2026-08-11 v0.2.0 Accessible worksheet]

\newif\ifaccess@sol       \access@solfalse
\newif\ifaccess@ans       \access@ansfalse
\newif\ifaccess@guidance  \access@guidancefalse
\newif\ifaccess@draft     \access@draftfalse
\newif\ifaccess@house     \access@housefalse

\DeclareOption{blank}{\access@solfalse\access@ansfalse}
\DeclareOption{ans}{\access@anstrue}
\DeclareOption{sol}{\access@soltrue\access@anstrue}
\DeclareOption{guidance}{\access@guidancetrue}
\DeclareOption{housecolors}{\access@housetrue}
\DeclareOption{draft}{\access@drafttrue}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{article}}
\ProcessOptions\relax

\LoadClass[11pt]{article}

\RequirePackage{hyperref}
\RequirePackage{bookmark}

\ifaccess@house
  \PassOptionsToPackage{housecolors}{latexa11y-doc}
\fi
\RequirePackage{latexa11y-doc}

\ifaccess@sol\accessshowsolutions\fi
\ifaccess@ans\accessshowanswers\fi
\ifaccess@guidance\accessshowguidance\fi
\ifaccess@draft\accesssetup{strict=false}\fi

\endinput
