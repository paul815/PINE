# whisper-large-v3__fp16

| | |
|---|---|
| модель | `F:/Downloads/AI Stuff/pine-Mac_Improvements/PINE-RC-v1.0.0/models/whisperx-large-v3` |
| время расшифровки | 118.3 с |
| пик VRAM | 6484 МБ |
| сегментов | 225 |
| пословных таймингов | 3071 |

---

**00:00**  represent per page so like each string represents whatever is on an entire page

**00:07**  and then the last page that the user looked at this would just be some

**00:11**  integer and this would be an index into this list of strings so I would have to

**00:16**  remember that this you know there might be an off by one error here because in

**00:24**  Python we start at zero but like naturally when we're reading a book we

**00:28**  might start at one yep now for this library for this collection of books

**00:34**  okay well so for this active book I you could either use a boolean where you set

**00:41**  one book to active in the rest of two inactive but I think that that would be

**00:46**  kind of excessive because every single time we toggle a book as active we have

**00:51**  to toggle everything else as inactive right you can't have two active books

**00:55**  yeah yeah okay

**00:58**  okay

**00:58**  yeah that makes sense okay so then I think in that case instead what I would

**01:03**  rather do is have some sort of a string or some sort of like an ID matching

**01:11**  system where we have like one variable that's set to the active book and that

**01:19**  might be the title or the ID of a certain book in the library so maybe

**01:25**  maybe we can just add ID here

**01:27**  um and maybe I can make that also a string or an integer or something yep

**01:33**  and I'm gonna add a question mark to kind of just say like maybe we could

**01:38**  implement that but I'm not sure if we'd want to right now um so this would

**01:43**  correspond to ID so this would just be some variable and then this collection of

**01:57**  books I'm gonna be doing it again which is a collection of books because we're

**01:58**  So, I guess like when we display this page in an active book, I actually, so I think that this might have an advantage being like some sort of a lookup table because we have a certain sense of an ID, right?

**02:21**  And I think what could make sense here is having the ID correspond to the book object that we define up here.

**02:28**  So, this could be a book object, right?

**02:32**  And so, we don't really need this ID.

**02:35**  I think that if all the titles are unique, then we don't need this ID.

**02:40**  We can just use the titles as the IDs.

**02:44**  But in some cases, in the real world, not all titles are unique.

**02:48**  And so, that's when an ID might come in handy.

**02:53**  And so, I'll leave that like, what are our assumptions here?

**02:57**  Can we assume?

**02:58**  That the titles are unique or should I be using this ID structure?

**03:02**  I think I like the ID structure because, yeah, we're not necessarily going to know that everything's unique.

**03:06**  So, I think this is a little bit more robust.

**03:10**  Okay.

**03:11**  Awesome.

**03:11**  So, I think this representation looks good to me.

**03:15**  So, I'm just going to go through the requirements again just to make sure that I've covered all my bases.

**03:21**  So, here I want all books.

**03:24**  Okay.

**03:25**  So, I want a library of books that I can add to or remove from.

**03:28**  And so, here I have a library.

**03:29**  And anytime I want to add to this library, I can just add an ID.

**03:33**  I can add the book.

**03:34**  If I want to remove from the library, then I can just simply delete the key item pair or the key whatever pair, key value pair.

**03:48**  I can set a book from their library as active.

**03:51**  And so, here I have this active book.

**03:53**  And we could always say, like, if there is no active book, then we can just remove it.

**03:58**  We can set this to none or something like that.

**04:00**  The reading application remembers where a user left off.

**04:04**  So, we have that here, right?

**04:05**  The last page that the user looked at and not just in the active book.

**04:10**  And then displaying one page of text at a time in the active book.

**04:15**  So, we have the active book.

**04:17**  We can get the book object from that.

**04:19**  And then we can go to the page that the user last looked at by indexing this value into this list.

**04:28**  So, I think we've covered all the bases here.

**04:30**  Cool.

**04:32**  Let's do a couple things.

**04:34**  I guess, I think as a starting point, and this doesn't have to get too crazy, but I would love to just see some, you know, Python pseudocode or even some Python kind of code.

**04:44**  Just to, like, maybe flesh out one of these classes.

**04:46**  I just want to, you know, look at that a little bit and how you would actually start writing this code.

**04:52**  Yeah, sure.

**04:53**  So, for example, here, let's start with the book.

**04:59**  And when we define a class, let's initialize this.

**05:05**  So, when we initialize a book, we definitely should pass in the title and then the content, right?

**05:13**  I'm assuming that when we add a book, these things have to be given to us.

**05:17**  And then I'm assuming that when we add a book, we don't have a last page that the user has looked at.

**05:24**  Is that an okay assumption?

**05:25**  Yeah, I like that assumption.

**05:27**  Okay.

**05:28**  So, we can do that.

**05:29**  We can set the title of this book just equal to whatever the title is, the content of this book.

**05:36**  And I'm going to assume that these are given to us in the formatting that we want.

**05:42**  So, the title is a string and the content is a list of strings that correspond to the pages.

**05:47**  And I'm going to create a variable last page, and I'm going to set that equal to just zero.

**05:54**  I could also probably set it equal to negative one or something, but I think zero makes sense because that would be the beginning.

**05:59**  Right?

**06:00**  Yep.

**06:02**  And then we need to come up with some sort of an ID.

**06:06**  So, typically, maybe we would have some sort of function that takes in the title and the content or maybe the author or something and returns a unique ID.

**06:18**  But I think maybe something that we can do here just very simply is have some sort of counter in the library.

**06:27**  And then every single time we add.

**06:29**  Add a new book, we can just associate that counter with that book.

**06:34**  And so, then we know that they're all unique and they all have their own IDs.

**06:41**  Would that make sense?

**06:42**  Yeah, that makes sense.

**06:44**  Okay.

**06:45**  So, then I would have to pass in the ID.

**06:49**  Yeah.

**06:50**  And so, what I'm actually going to do is self.id equals ID.

**06:56**  And then.

**06:59**  Yeah, and so, then, for example, we might want like a display page, right, in this book.

**07:06**  And so, for that, then we just want to return whatever is at the last page of the content.

**07:17**  And I guess let's build on this a little bit.

**07:20**  Another useful function, you know, obviously, you want to start at the last page.

**07:24**  But let's say we're starting to turn the page.

**07:26**  What might that look like?

**07:29**  Right.

**07:30**  And so.

**07:30**  So, if we're turning the page, then all we want to do is increment the last page, right?

**07:39**  And so, we might do self.lastPage.

**07:43**  And we just increment that by one.

**07:45**  And then we could call displayPage after that.

**07:51**  If we assume that, like, we could.

**07:55**  If we assume that these two go together.

**07:57**  So, if we assume that when we turn the page, we want to display that page.

**08:01**  Then I would just return displayPage.

**08:03**  So, if we assume that when we turn the page, we want to display displayPage.

**08:05**  Yep.

**08:06**  Like that.

**08:10**  And then, actually, I'm just going to go ahead and code up this library as well.

**08:16**  So, this library, we have this collection of books and then also the active book.

**08:24**  So, rather than initializing a library with books, I think let's just add them to this.

**08:33**  So, initially, this collection might just be an empty database.

**08:39**  So, I'm just going to call this library dictionary and then the active book might be none, right?

**08:44**  So, then when we want to add stuff.

**08:50**  So, add to collection book.

**09:00**  What I'm going to do.

**09:02**  So, there's either the user could, like, or our API could, like, return a book with the title and the content.

**09:15**  But since we're, we said that we would pass the ID through, like, the library because we might have, like, a counter.

**09:23**  Like, some sort of ID counter.

**09:24**  I mean, this is not the best way to do it, but I think this is, like, a fine hack for right now.

**09:31**  Just to make all the IDs unique.

**09:33**  What we can do here is we can also, when we want to add a book, just pass in the title and the content.

**09:43**  And so, then.

**09:45**  new book is going to be a book as we've defined above with the ID and the title

**09:55**  and the count or the content and then after we create this new book we want to

**10:03**  increment the ID counter and then we also want to add this new book into the

**10:09**  collection right so I'm gonna add self dot collection and I'm going to make the

**10:19**  ID whatever the ID of the book is so actually I'm just going to call new book

**10:24**  dot ID because I think that's a bit cleaner and this is going to be the new

**10:28**  book and then we increment the counter by one so this is us adding to the

**10:32**  collection so of course we want to remove from the

**10:38**  collection

**10:39**  and so we want we said that we wanted to remove based on the id so all we have to do is i think

**10:48**  there's this delete in python i'm not sure if there is do you know keith

**10:58**  okay is there a remove let's i think that there's i mean it should autocomplete here in

**11:05**  the coder pad oh isn't it del isn't there a del um i mean i think the biggest thing is is it going

**11:13**  to be happening in place or is it going to be happening in creating a copy um i i understand

**11:19**  yeah so we want it in place for the collection i understand what you're trying to do so the

**11:24**  exact details is is not super super important to me okay yeah so anyways this is removing from

**11:31**  the collection um and when we set active

**11:35**  book again we want to do this based on the id so i'm going to make self.active book

**11:43**  equal to whatever that id is uh and let's see so the user has a library of books they can add or

**11:56**  remove from we did that setting a book as active okay and then remembering where a user left off

**12:02**  so we've actually taken care of that already implicitly in our book class and then the

**12:08**  reading application only displays a page of text at a time all right so then here we can say define

**12:15**  display page and actually we don't even need the the id of the book because we already have the

**12:23**  active book id right and so what i'm going to do is i'm going to go into my collection

**12:30**  um and i'm going to get the

**12:32**  active book like this so yep blah blah and i'm going to click or i'm going to do dot display page

**12:44**  and then of course we also have this turn page that i could um just put up as well yep you get

**13:01**  the idea but so we want to oops turn that page um i do think that some like drawbacks here are that

**13:16**  uh okay so one thing that we could have done is instead of this active book instead of saving the

**13:23**  id we could have saved the book itself and i think from a pythonic standpoint that might make sense

**13:28**  but then in the future if we're using some sort of database or something an id would make more

**13:33**  sense because that's easier to store in like a table um and if uh and okay also for this display

**13:41**  page you might see that we've called this display page and turn page

**13:45**  up here in the book and the reason why i did that rather than um rather than just call like

**13:54**  self dot book dot content last page like book dot last page or something was because i think that

**14:02**  like for example this should be independent of how we implement the book right so we want to

**14:08**  keep those as kind of block like separate blocks as possible and so here

**14:15**  what this allows me to do is when i go back to my book like if i decide to scrap this implementation

**14:21**  implement something else i know that i would just have to implement the methods display page and

**14:25**  turn page for that to work um and like these functions cool this this looks good to me i like

**14:33**  the start i think that this uh definitely lays out the foundation of what i was looking for with

**14:39**  the requirements of this application so nice work with this couple quick follow-up questions before

**14:44**  we kind of branch into

**14:46**  more of an algorithms uh type question as as the follow-up here um one question is you know let's

**14:54**  say you have an older reader and their vision isn't as great so they need to make this the size

**15:00**  of the font bigger so kind of keeping that in mind that you might have this variable font size

**15:05**  how might you i guess refactor or change or modify your current structure and you don't

**15:11**  have to actually write code here let's just discuss this to account for

**15:16**  um that increase in size yeah yeah so i so when we increase something in size like i'm just thinking

**15:26**  about intuitively on our like phone screens or something um that type that tends to shift the

**15:31**  content right so like some page that might hold a hundred words or a hundred characters at you know

**15:38**  um a small font size might only hold 50 if when we double that font size so

**15:45**  for the book instead of having this list what i might do is i might just save the entire book as

**15:54**  a string and um and based so i would let me just edit this in here because i think it's easier to

**16:01**  visualize what i'm saying but so i would might i might have like a font size equal to um i guess

**16:12**  we could just use like the traditional like 12 point font or like yeah whatever uh but

**16:18**  when we have this font size we could come up with some sort of um characters per page calculation

**16:26**  right and so that would be based off the font size so calculate so this is going to be pseudocode

**16:32**  now this calculate doesn't actually exist but calculate uh based on that font size

**16:40**  um and i'm commenting it out so it doesn't have any errors but then uh when we do

**16:47**  calculate that then what we can do in order to get our um in order to get this display page

**16:54**  thing would be instead of indexing into this content which is now

**16:58**  just a long string of characters what we can do here is we can return um we would have to do some

**17:09**  sort of calculation right so we would need to we have this characters per page and we can multiply

**17:17**  that by whatever the last page is and so this would give us like kind of the

**17:26**  starting index of this long string of characters to start the content of that

**17:32**  page start index would be this and then what we would want to do is what we

**17:44**  want to do is then return the self dot content from that start index until the

**17:51**  start index plus the number of characters on that page so that would be

**17:57**  our end index so actually let me just write that out to make things a little

**18:01**  bit more clear so now essentially what I've done is we have this variable font

**18:11**  size which we can calculate characters per page depending on that font size so

**18:16**  that might be more characters or less characters depending on how big the font

**18:20**  size is

**18:21**  and then our content instead of a list of strings is now just a really long

**18:25**  string of characters and I guess some assumptions that I'm making here are

**18:29**  that this would just be approximate so for example if we had a backslash n

**18:35**  which is like a line break right that might just end up counting as a

**18:39**  character or two characters but the assumption is that that would be

**18:45**  approximate per page or maybe that we're using some sort of like monospaced font

**18:50**  or something like that

**18:52**  but essentially now that we have the characters per page and then just a long

**18:56**  string of characters that represents the content we can index into that content

**19:00**  depending on like based on the last page that we know and so what's really cool

**19:07**  here is that when we do turn the page we can just end up calling it so this is

**19:12**  kind of that granularity that I was talking about yeah that that makes a lot

**19:15**  of sense to me and definitely you see the advantage of having that function

**19:19**  that you can change.

**19:21**  by having to implement something different one last quick I guess follow

**19:25**  up and then we'll move on to the algorithms portion of this interview I'm

**19:30**  just trying to think you know at a high level let's say that we wanted we had

**19:34**  multiple users in this system and they all shared books so like you know they

**19:39**  might all share the Harry Potter books or something like that like books are

**19:43**  common among all of them

**19:44**  how might you modify this yeah given that you know some of these qualities of

**19:49**  a book might be shared between you and someone else.

**19:51**  users because they're all trying to access that same book does that make

**19:55**  sense yeah so um yeah yeah for sure so I
