# Welcome to the ROSACE project

The project is developed by the community, and your contrubution is important!

Here are the ways to help developing this tool:

- Install your own observatory and give us any user feedback

- If you’re a developer, you can improve the system. Contact us and tell us if you need any help (there is so much to do!).

- Write and/or translate the documentation (the initial plan is to get only an english version, but we never know)

### Creating your own plugins

You can create (or adapt) your own plugins to ROSACE, to fit your hardware setup and your science program. The plugins can apply for Observing Sequence, Data Reduction or Sharing step. 

The simplest way to create a plugin is to duplicate the blank plugins (either blank_seq, blank_red or blank_share). For instance, the blank_seq.py file is in ...ROSACE/rosace/rosace/plugins/sequencer_plugins/blank_seq/blank_seq.py.

The plugin manages the logging tool (log = logging.getLogger("spectro.subplugin.YOUR_PUGIN")).

It also receives the Observation structure, that contains all the informations collected so far during the observation, and you can add your own data (if they are useful for next steps, or for keeping the memory of the observation).

Then, you must mention your plugin in the Science Program file (Your_program.yaml).

Note that your plugin must have a non-existing name, to avoid any confusion. A test will detect if your new name is already existing. 

You'll find more information in the ROSACE documentation: [](https://rosace-astronomy.github.io/rosace-docs/#welcome-to-the-rosace-project)


Please send any comment or question to François Cochard ([francois.cochard@shelyak.com](mailto:francois.cochard@shelyak.com))